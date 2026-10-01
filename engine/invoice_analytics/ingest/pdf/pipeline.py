"""Document ingest: text layer or OCR -> template/heuristic extraction -> confidence + arithmetic
cross-check -> auto-accept or an extraction draft for a person to correct. Corrections become
vendor templates. Low-confidence drafts get an optional T1 (LLM) extraction job when AI is on.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import re
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.ingest.model import InvoiceIn, LineIn, ParseIssue, ParseResult, PartyIn, PatientIn
from invoice_analytics.normalize import AmbiguousDateError, AmountError, DateError, parse_amount, parse_date
from invoice_analytics.security import audit

EXTRACT_TIMEOUT_S = 120
REQUIRED = ("invoice_number", "invoice_date", "total", "vendor_name")


def _run_isolated(data: bytes, kind: str, templates: dict[str, Any], in_process: bool) -> dict[str, Any]:
    from invoice_analytics.ingest.pdf.extract import extract

    if in_process:
        return extract(data, kind, templates)
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
        fut = ex.submit(extract, data, kind, templates)
        try:
            return fut.result(timeout=EXTRACT_TIMEOUT_S)
        except FutTimeout:
            for p in list(getattr(ex, "_processes", {}).values()):
                p.kill()
            raise


def _templates(eng: Engine) -> dict[str, Any]:
    return {
        r["fingerprint"]: json.loads(r["template"])
        for r in eng.db.query("SELECT fingerprint, template FROM vendor_templates ORDER BY hits")
    }


def score_draft(fields: dict[str, Any], page_conf: float) -> dict[str, Any]:
    conf = dict(fields.get("confidence") or {})
    for f in REQUIRED:
        if not fields.get(f):
            conf[f] = 0.0
        conf[f] = min(conf.get(f, 0.0), max(page_conf, 0.0) if page_conf < 0.9 else conf.get(f, 0.0))
    total_c = None
    try:
        total_c = parse_amount(fields["total"]) if fields.get("total") else None
    except AmountError:
        conf["total"] = 0.0
    line_sum = 0
    ok_lines = True
    for li in fields.get("lines") or []:
        try:
            line_sum += parse_amount(li.get("amount"))
        except AmountError:
            ok_lines = False
    arithmetic = {
        "lines_sum_cents": line_sum,
        "total_cents": total_c,
        "ok": bool(ok_lines and total_c is not None and fields.get("lines") and line_sum == total_c),
    }
    if total_c is not None and fields.get("lines"):
        if arithmetic["ok"]:
            conf["total"] = max(conf.get("total", 0.0), 0.98)
        else:
            conf["total"] = min(conf.get("total", 0.0), 0.5)
    overall = min(conf.get(f, 0.0) for f in REQUIRED)
    return {"confidence": conf, "overall": round(overall, 3), "checks": {"arithmetic": arithmetic}}


def draft_to_parse(fields: dict[str, Any], options: dict[str, Any], method: str) -> ParseResult:
    pr = ParseResult(method=method)
    inv_date = None
    attn: list[str] = []
    try:
        inv_date = (
            # the Ingest screen declares the files' date order (default MM/DD/YYYY), so it is not a guess
            parse_date(
                fields.get("invoice_date"),
                prefer=options.get("date_order", "US"),
                strict=bool(options.get("strict_dates", False)),
            ).iso
            if fields.get("invoice_date")
            else None
        )
    except AmbiguousDateError as e:
        attn.append(str(e))
    except DateError as e:
        pr.issues.append(ParseIssue("invoice_date", str(e)))
    try:
        total = parse_amount(fields["total"]) if fields.get("total") else None
    except AmountError as e:
        pr.issues.append(ParseIssue("total", str(e)))
        total = None
    lines = []
    for n, li in enumerate(fields.get("lines") or [], start=1):
        try:
            amt = parse_amount(li.get("amount"))
        except AmountError:
            pr.issues.append(ParseIssue(f"line {n}", "invalid amount", "WARNING"))
            continue
        dos = None
        if li.get("dos"):
            try:
                dos = parse_date(li["dos"], prefer=options.get("date_order", "US"), strict=False).iso
            except DateError:
                dos = None
        try:
            units = float(li.get("units") or 1)
        except ValueError:
            units = 1.0
        subject = (li.get("subject") or "").strip() or None
        lines.append(
            LineIn(
                n,
                PatientIn(subject_id=subject) if subject else None,
                dos or inv_date,
                dos or inv_date,
                li.get("code") or li.get("item"),  # trial invoices: the service item stands in for a code
                li.get("modifiers") or [],
                units,
                amt,
                description=li.get("description"),
                visit=li.get("visit"),
            )
        )
    pr.invoices.append(
        InvoiceIn(
            direction=options.get("direction", "AP"),
            party=PartyIn(
                fields.get("vendor_name") or "UNKNOWN",
                options.get("party_type", "VENDOR"),
                fields.get("tax_id"),
                fields.get("npi"),
            ),
            invoice_number=fields.get("invoice_number"),
            invoice_date=inv_date,
            invoice_date_raw=fields.get("invoice_date"),
            total_cents=total,
            po_number=fields.get("po_number"),
            lines=lines,
            needs_attention=attn,
        )
    )
    return pr


def extract_document(
    eng: Engine, data: bytes, name: str, *, kind: str, options: dict[str, Any], user_id: int | None = None
) -> ParseResult:
    in_process = bool(options.get("_in_process"))
    try:
        ex = _run_isolated(data, kind, _templates(eng), in_process)
    except FutTimeout:
        return ParseResult(method="PDF_TEXT", issues=[ParseIssue("document", "extraction timed out")])
    except Exception as e:  # noqa: BLE001 - malformed documents must not crash ingest
        return ParseResult(
            method="PDF_TEXT", issues=[ParseIssue("document", f"could not read document: {type(e).__name__}")]
        )
    fields = ex["fields"]
    if fields.get("vendor_name"):
        from invoice_analytics.normalize import normalize_party_name

        if eng.db.scalar(
            "SELECT 1 FROM parties WHERE name_norm=? UNION SELECT 1 FROM party_aliases WHERE"
            " alias_type='NAME' AND value_norm=?",
            (normalize_party_name(fields["vendor_name"]),) * 2,
        ):
            fields.setdefault("confidence", {})["vendor_name"] = 0.95  # a known party
    sc = score_draft(fields, ex["page_confidence"])
    threshold = float(eng.settings.get("ai.extract_confidence_threshold", 0.85))
    draft = {
        "invoice": {
            k: fields.get(k)
            for k in (
                "vendor_name",
                "invoice_number",
                "invoice_date",
                "due_date",
                "total",
                "po_number",
                "tax_id",
                "npi",
            )
        },
        "lines": fields.get("lines", []),
        "confidence": sc["confidence"],
        "overall": sc["overall"],
        "checks": sc["checks"],
        "method": ex["method"],
        "fingerprint": ex["fingerprint"],
        "template_used": ex["template_used"],
        "page_count": ex["page_count"],
        "text": ex["text"][:20000],
    }
    method = ex["method"]
    sha = hashlib.sha256(data).hexdigest()
    if sc["overall"] >= threshold and sc["checks"]["arithmetic"]["ok"]:
        pr = draft_to_parse({**draft["invoice"], "lines": draft["lines"]}, options, method)
        pr.confidence = sc["overall"]
        pr.page_count = ex["page_count"]
        pr.meta["draft_fields"] = draft  # stored as an ACCEPTED draft once the document row exists
        return pr
    # Needs a person: store the document + an OPEN draft; nothing is persisted as an invoice yet.
    with eng.db.tx() as c:
        doc_id = int(
            c.execute(
                "INSERT INTO documents(source_path, sha256, mime, page_count, ingest_method, ingest_status,"
                " ingest_confidence, ingested_by, ingested_at) VALUES (?,?,?,?,?,'NEEDS_REVIEW',?,?,"
                " strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (name, sha, kind, ex["page_count"], method, sc["overall"], user_id),
            ).lastrowid
        )
        did = int(
            c.execute(
                "INSERT INTO extraction_drafts(document_id, method, fields) VALUES (?,?,?)",
                (doc_id, method, json.dumps(draft)),
            ).lastrowid
        )
        audit.record(
            eng.db,
            "EXTRACTION_DRAFT",
            user_id=user_id,
            entity_type="document",
            entity_id=doc_id,
            after={"draft_id": did, "overall_confidence": sc["overall"], "method": method},
            conn=c,
        )
    if eng.settings.get("ai.tier", "OFF") != "OFF":
        from invoice_analytics.ai.jobs import PRIORITY

        eng.jobs.enqueue("AI_EXTRACT", {"draft_id": did}, PRIORITY["SYSTEM"])
    return ParseResult(
        method=method,
        confidence=sc["overall"],
        page_count=ex["page_count"],
        meta={"needs_review_draft_id": did, "document_id": doc_id},
        issues=[
            ParseIssue(
                "document",
                f"extraction confidence {sc['overall']:.2f} is below " f"{threshold:.2f}; review the draft",
                "WARNING",
            )
        ],
    )


def learn_template(eng: Engine, fp: str, text: str, corrected: dict[str, Any], party_id: int | None) -> None:
    """Remember, for this layout, the label that precedes each corrected value."""
    anchors: dict[str, list[str]] = {}
    for f in ("invoice_number", "invoice_date", "due_date", "total", "po_number", "tax_id", "npi"):
        v = corrected.get(f)
        if not v:
            continue
        for line in text.splitlines():
            idx = line.find(str(v))
            if idx > 0:
                label = re.sub(r"[:#\s]+$", "", line[:idx]).strip()
                label = " ".join(label.split()[-3:])
                if 2 <= len(label) <= 40:
                    anchors.setdefault(f, []).append(label)
                    break
    tmpl = {"anchors": anchors, "vendor_name": corrected.get("vendor_name")}
    with eng.db.tx() as c:
        row = c.execute("SELECT id, template FROM vendor_templates WHERE fingerprint=?", (fp,)).fetchone()
        if row:
            old = json.loads(row["template"])
            for k, v in anchors.items():
                old.setdefault("anchors", {}).setdefault(k, [])
                old["anchors"][k] = list(dict.fromkeys(v + old["anchors"][k]))[:3]
            if corrected.get("vendor_name"):
                old["vendor_name"] = corrected["vendor_name"]
            c.execute(
                "UPDATE vendor_templates SET template=?, hits=hits+1, party_id=COALESCE(?, party_id),"
                " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (json.dumps(old), party_id, row["id"]),
            )
        else:
            c.execute(
                "INSERT INTO vendor_templates(party_id, fingerprint, template, hits) VALUES (?,?,?,1)",
                (party_id, fp, json.dumps(tmpl)),
            )


def accept_draft(
    eng: Engine, draft_id: int, corrected: dict[str, Any], *, user_id: int | None, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    from invoice_analytics.ingest.persist import persist
    from invoice_analytics.ingest.service import run_incremental

    d = eng.db.one("SELECT * FROM extraction_drafts WHERE id=?", (draft_id,))
    if d is None or d["status"] != "OPEN":
        raise ValueError("draft not found or already decided")
    draft = json.loads(d["fields"])
    inv = {**draft["invoice"], **(corrected.get("invoice") or {})}
    lines = corrected.get("lines", draft["lines"])
    pr = draft_to_parse({**inv, "lines": lines}, options or {}, draft.get("method", "PDF_TEXT"))
    if corrected.get("from_ai"):
        pr.method = "LLM"
    doc = eng.db.one("SELECT sha256, source_path FROM documents WHERE id=?", (d["document_id"],))
    if doc is None:
        raise ValueError("document for draft not found")
    res = persist(
        eng,
        pr,
        sha256=doc["sha256"],
        source_path=doc["source_path"],
        mime="PDF",
        user_id=user_id,
        document_id=d["document_id"],
    )
    learn_template(
        eng,
        draft["fingerprint"],
        draft.get("text", ""),
        inv,
        eng.db.scalar("SELECT party_id FROM invoices WHERE id=?", (res.invoice_ids[0],)) if res.invoice_ids else None,
    )
    with eng.db.tx() as c:
        c.execute(
            "UPDATE extraction_drafts SET status='ACCEPTED', fields=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE id=?",
            (json.dumps({**draft, "invoice": inv, "lines": lines, "corrected_by": user_id}), draft_id),
        )
        audit.record(
            eng.db,
            "EXTRACTION_ACCEPT",
            user_id=user_id,
            entity_type="extraction_draft",
            entity_id=draft_id,
            after={"document_id": d["document_id"], "invoice_ids": res.invoice_ids},
            conn=c,
        )
    det = run_incremental(eng, res, user_id) if res.invoice_ids else None
    opts = options or {}
    if det and opts.get("hold_duplicates") and not opts.get("confirm_duplicates"):
        from invoice_analytics.ingest.service import hold_if_billed_before

        held = hold_if_billed_before(eng, res.invoice_ids, det, user_id=user_id, keep_documents=True)
        if held:
            with eng.db.tx() as c:  # back to the correction queue, keeping the user's corrections
                c.execute(
                    "UPDATE extraction_drafts SET status='OPEN', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                    " WHERE id=?",
                    (draft_id,),
                )
                c.execute("UPDATE documents SET ingest_status='NEEDS_REVIEW' WHERE id=?", (d["document_id"],))
            return {"persist": None, "detection": det, "held": held}
    return {"persist": res.as_dict(), "detection": det, "held": None}
