"""Ingest entry point: detect the file type, parse, persist, then run incremental detection."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.ingest.csv import CsvOptions, header_signature, parse_table, read_table, suggest_mapping
from invoice_analytics.ingest.csv.importer import MalformedFile
from invoice_analytics.ingest.model import ParseIssue, ParseResult
from invoice_analytics.ingest.persist import PersistResult, persist
from invoice_analytics.ingest.x12 import parse_x12
from invoice_analytics.normalize import normalize_invoice_number
from invoice_analytics.security import audit

log = logging.getLogger("invoice_analytics.ingest")

MAX_FILE_BYTES = 200 * 1024 * 1024
CSV_EXT = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")
X12_EXT = (".x12", ".edi", ".837", ".835", ".dat")
PDF_EXT = (".pdf",)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


class NeedsMapping(Exception):
    """A CSV/Excel file has no saved template; the UI must show the mapping wizard."""

    def __init__(self, headers: list[str], suggestion: dict[str, Any], preview: list[list[str]]) -> None:
        super().__init__("column mapping required")
        self.headers = headers
        self.suggestion = suggestion
        self.preview = preview


@dataclass
class IngestOutcome:
    persist: PersistResult | None
    parse: ParseResult
    detection: dict[str, Any] | None
    remittances_applied: int = 0
    seconds: float = 0.0
    held: dict[str, Any] | None = None  # set when the upload was held back as a possible duplicate

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.parse.method,
            "confidence": self.parse.confidence,
            "issues": [i.as_dict() for i in self.parse.issues[:200]],
            "persist": self.persist.as_dict() if self.persist else None,
            "detection": self.detection,
            "remittances_applied": self.remittances_applied,
            "seconds": round(self.seconds, 3),
            "held": self.held,
            "meta": {k: v for k, v in self.parse.meta.items() if k not in ("remittances", "draft_fields")},
        }


def sniff_kind(filename: str, data: bytes) -> str:
    name = filename.lower()
    head = data[:512].lstrip(b"\xef\xbb\xbf \r\n\t")
    if head.startswith(b"ISA"):
        return "X12"
    if head.startswith(b"%PDF") or name.endswith(PDF_EXT):
        return "PDF"
    if name.endswith(IMAGE_EXT) or head[:4] in (b"\x89PNG", b"II*\x00", b"MM\x00*") or head[:3] == b"\xff\xd8\xff":
        return "IMAGE"
    if name.endswith(X12_EXT):
        return "X12"
    if head[:2] == b"PK" and name.endswith((".xlsx", ".xlsm")):
        return "CSV"
    if name.endswith(CSV_EXT):
        return "CSV"
    return "UNKNOWN"


def find_template(eng: Engine, headers: list[str]) -> dict[str, Any] | None:
    sig = header_signature(headers)
    row = eng.db.one("SELECT * FROM csv_mapping_templates WHERE header_sig=? ORDER BY updated_at DESC LIMIT 1", (sig,))
    return row


def save_template(
    eng: Engine,
    name: str,
    headers: list[str] | None,
    mapping: dict[str, str],
    options: dict[str, Any],
    user_id: int | None,
) -> int:
    sig = header_signature(headers) if headers else None
    with eng.db.tx() as c:
        cur = c.execute(
            "INSERT INTO csv_mapping_templates(name, header_sig, mapping, options, created_by) VALUES (?,?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET header_sig=excluded.header_sig, mapping=excluded.mapping,"
            " options=excluded.options, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (name, sig, json.dumps(mapping), json.dumps(options), user_id),
        )
        tid = int(
            cur.lastrowid or c.execute("SELECT id FROM csv_mapping_templates WHERE name=?", (name,)).fetchone()["id"]
        )
        audit.record(
            eng.db,
            "CSV_TEMPLATE_SAVE",
            user_id=user_id,
            entity_type="csv_template",
            entity_id=tid,
            after={"name": name, "mapping": mapping},
            conn=c,
        )
    return tid


def ingest_bytes(
    eng: Engine,
    filename: str,
    data: bytes,
    *,
    user_id: int | None = None,
    mapping: dict[str, str] | None = None,
    template_id: int | None = None,
    options: dict[str, Any] | None = None,
    detect: bool = True,
) -> IngestOutcome:
    t0 = time.perf_counter()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    options = dict(options or {})
    sha = hashlib.sha256(data).hexdigest()
    kind = sniff_kind(filename, data)
    safe_name = Path(filename).name  # never trust client-supplied directories
    if kind == "CSV":
        try:
            table = read_table(data, safe_name, options.get("sheet"))
        except MalformedFile as e:
            return IngestOutcome(
                None, ParseResult(method="CSV", issues=[ParseIssue("file", str(e))]), None, 0, time.perf_counter() - t0
            )
        tmpl = None
        if template_id is not None:
            tmpl = eng.db.one("SELECT * FROM csv_mapping_templates WHERE id=?", (template_id,))
        elif mapping is None:
            tmpl = find_template(eng, table.headers)
        if tmpl is not None:
            mapping = json.loads(tmpl["mapping"])
            options = {**json.loads(tmpl["options"] or "{}"), **options}
        if mapping is None:
            raise NeedsMapping(table.headers, suggest_mapping(table.headers), table.rows[:5])
        pr = parse_table(table, mapping, CsvOptions.from_dict(options))
    elif kind == "X12":
        pr = parse_x12(data.decode("latin-1"), direction=options.get("direction", "AP"))
    elif kind in ("PDF", "IMAGE"):
        from invoice_analytics.ingest.pdf.pipeline import extract_document

        pr = extract_document(eng, data, safe_name, kind=kind, options=options)
    else:
        pr = ParseResult(method="CSV", issues=[ParseIssue("file", f"unsupported file type: {safe_name}")])
    remits = pr.meta.get("remittances") or []
    applied = apply_remittances(eng, remits, user_id) if remits else 0
    if not pr.invoices and not remits:
        if pr.meta.get("document_id") and kind in ("PDF", "IMAGE"):
            store_blob(eng, int(pr.meta["document_id"]), data)
        return IngestOutcome(None, pr, None, 0, time.perf_counter() - t0)
    pres = persist(eng, pr, sha256=sha, source_path=safe_name, mime=kind, user_id=user_id)
    det = None
    if kind in ("PDF", "IMAGE"):
        store_blob(eng, pres.document_id, data)
    if pr.meta.get("draft_fields"):
        with eng.db.tx() as c:
            c.execute(
                "INSERT INTO extraction_drafts(document_id, method, fields, status) VALUES (?,?,?,'ACCEPTED')",
                (pres.document_id, pr.method, json.dumps(pr.meta["draft_fields"])),
            )
    if detect and pres.invoice_ids:
        det = run_incremental(eng, pres, user_id)
        if options.get("hold_duplicates") and not options.get("confirm_duplicates"):
            held = hold_if_billed_before(eng, pres.invoice_ids, det, user_id=user_id)
            if held:
                return IngestOutcome(None, pr, det, applied, time.perf_counter() - t0, held=held)
    return IngestOutcome(pres, pr, det, applied, time.perf_counter() - t0)


def hold_if_billed_before(
    eng: Engine,
    invoice_ids: list[int],
    det: dict[str, Any] | None,
    *,
    user_id: int | None,
    keep_documents: bool = False,
) -> dict[str, Any] | None:
    """Interactive uploads: if anything on the new invoices was already billed on another invoice,
    take them back out again and describe the matches, so nothing is saved until the user confirms.

    The upload is ingested and checked exactly as a confirmed one would be (same rules, same
    thresholds); only then is it rolled back. Returns None when nothing matched."""
    from invoice_analytics.data_admin import delete_invoices

    repeats = ((det or {}).get("history") or {}).get("repeats") or []
    if not repeats:
        return None
    ph = ",".join("?" * len(invoice_ids))
    flag_ids = [r["flag_id"] for r in repeats]
    fph = ",".join("?" * len(flag_ids))
    new = eng.db.query(
        f"SELECT i.id, i.invoice_number_raw AS number, i.invoice_date AS date, i.total_cents, p.display_name AS party"
        f" FROM invoices i JOIN parties p ON p.id=i.party_id WHERE i.id IN ({ph}) ORDER BY i.id",
        list(invoice_ids),
    )
    rows = eng.db.query(
        f"SELECT f.subject_type, f.subject_id, f.counterpart_invoice_ids FROM flags f WHERE f.id IN ({fph})", flag_ids
    )
    earlier_ids = sorted({int(x) for r in rows for x in json.loads(r["counterpart_invoice_ids"])} - set(invoice_ids))
    eph = ",".join("?" * len(earlier_ids)) or "NULL"
    earlier = eng.db.query(
        f"SELECT i.id, i.invoice_number_raw AS number, i.invoice_date AS date, i.total_cents, p.display_name AS party"
        f" FROM invoices i JOIN parties p ON p.id=i.party_id WHERE i.id IN ({eph}) ORDER BY i.invoice_date, i.id",
        earlier_ids,
    )
    lines_matched = len({r["subject_id"] for r in rows if r["subject_type"] == "LINE"})
    held = {
        "invoices": new,
        "earlier_invoices": earlier,
        "lines_checked": ((det or {}).get("history") or {}).get("lines", 0),
        "lines_matched": lines_matched,
        "repeat_count": len(repeats),
        "repeats": [{k: r[k] for k in ("rule_id", "tier", "summary")} for r in repeats[:50]],
    }
    delete_invoices(
        eng,
        list(invoice_ids),
        user_id=user_id,
        audit_action="INGEST_HELD_DUPLICATE",
        keep_documents=keep_documents,
        drop_empty_parties=True,
    )
    return held


def run_incremental(eng: Engine, pres: PersistResult, user_id: int | None) -> dict[str, Any]:
    from invoice_analytics.linkage.patients import quick_link_new_patients
    from invoice_analytics.scoring.pipeline import detect

    if pres.new_patient_ids:
        quick_link_new_patients(eng, pres.new_patient_ids)
    eng.store.upsert_invoices(pres.invoice_ids)
    if pres.new_patient_ids:
        eng.store.refresh_clusters()
    res = detect(eng, pres.invoice_ids, user_id=user_id)
    return {**res.stats(), "history": history_check(eng, pres.invoice_ids)}


def history_check(eng: Engine, invoice_ids: list[int]) -> dict[str, Any]:
    """The answer to "has anything on this upload been billed before?" for the ingest screen: how many
    lines and patients/subjects were checked, how many earlier lines exist for those patients on other
    invoices, and the flags that link this upload to another invoice."""
    if not invoice_ids:
        return {"lines": 0, "patients": 0, "earlier_lines": 0, "earlier_invoices": 0, "repeats": []}
    ph = ",".join("?" * len(invoice_ids))
    ids = list(invoice_ids)
    lines = eng.db.scalar(f"SELECT COUNT(*) FROM invoice_lines WHERE invoice_id IN ({ph}) AND deleted_at IS NULL", ids)
    clusters = [
        r["c"]
        for r in eng.db.query(
            f"SELECT DISTINCT COALESCE(pt.cluster_id, pt.id) AS c FROM invoice_lines l JOIN patients pt"
            f" ON pt.id=l.patient_id WHERE l.invoice_id IN ({ph}) AND l.deleted_at IS NULL",
            ids,
        )
    ]
    earlier = {"n": 0, "inv": 0}
    if clusters:
        cp = ",".join("?" * len(clusters))
        earlier = (
            eng.db.one(
                f"SELECT COUNT(*) AS n, COUNT(DISTINCT l.invoice_id) AS inv FROM invoice_lines l"
                f" JOIN patients pt ON pt.id=l.patient_id JOIN invoices i ON i.id=l.invoice_id"
                f" WHERE COALESCE(pt.cluster_id, pt.id) IN ({cp}) AND l.invoice_id NOT IN ({ph})"
                f" AND l.deleted_at IS NULL AND i.deleted_at IS NULL",
                [*clusters, *ids],
            )
            or earlier
        )
    repeats = [
        {"flag_id": r["id"], "rule_id": r["rule_id"], "tier": r["tier"], "summary": r["summary"]}
        for r in eng.db.query(
            f"SELECT f.id, f.rule_id, f.tier, json_extract(f.evidence,'$.summary') AS summary FROM flags f"
            f" WHERE f.subject_invoice_id IN ({ph}) AND f.active=1 AND f.suppressed_by IS NULL"
            f" AND f.tier IN ('HARD','PROBABLE') AND f.counterpart_invoice_ids NOT IN ('[]','')"
            f" AND json(f.counterpart_invoice_ids) <> json_array(f.subject_invoice_id)"
            f" ORDER BY CASE f.tier WHEN 'HARD' THEN 0 WHEN 'PROBABLE' THEN 1 WHEN 'WEAK' THEN 2 ELSE 3 END, f.score DESC",
            ids,
        )
    ]
    return {
        "lines": int(lines or 0),
        "patients": len(clusters),
        "earlier_lines": int(earlier["n"] or 0),
        "earlier_invoices": int(earlier["inv"] or 0),
        "repeats": repeats,
    }


def apply_remittances(eng: Engine, remits: list[dict[str, Any]], user_id: int | None) -> int:
    """835: mark matching claims PAID and record line-level paid amounts."""
    n = 0
    touched: list[int] = []
    with eng.db.tx() as c:
        for r in remits:
            num = normalize_invoice_number(r.get("claim_id"))
            if not num:
                continue
            invs = c.execute(
                "SELECT id FROM invoices WHERE invoice_number_norm=? AND deleted_at IS NULL", (num,)
            ).fetchall()
            for inv in invs:
                status = "PAID" if r.get("paid_cents", 0) > 0 else None
                if r.get("status_code") == "22":  # reversal of previous payment
                    status = "OPEN"
                if status:
                    c.execute(
                        "UPDATE invoices SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                        (status, inv["id"]),
                    )
                for li in r.get("lines", []):
                    c.execute(
                        "UPDATE invoice_lines SET paid_cents=? WHERE invoice_id=? AND code=?"
                        " AND (? IS NULL OR dos_from=?)",
                        (li.get("paid_cents"), inv["id"], li.get("code"), li.get("dos"), li.get("dos")),
                    )
                touched.append(inv["id"])
                n += 1
        audit.record(
            eng.db,
            "REMITTANCE_APPLY",
            user_id=user_id,
            entity_type="invoice",
            after={"claims": len(remits), "matched": n},
            conn=c,
        )
    if touched:
        eng.store.upsert_invoices(touched)
    return n


def store_blob(eng: Engine, document_id: int, data: bytes) -> None:
    with eng.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO document_blobs(document_id, data) VALUES (?,?)", (document_id, data))
