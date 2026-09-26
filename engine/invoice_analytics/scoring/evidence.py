"""Evidence builder (spec 5.5). The UI renders this; the AI layer consumes it verbatim."""

from __future__ import annotations

import datetime as dt
from typing import Any

from invoice_analytics.normalize import format_cents
from invoice_analytics.rules.base import Candidate, Rule

INVOICE_FIELDS = [
    ("party", "party"),
    ("invoice_number", "invoice_number"),
    ("invoice_date", "invoice_date"),
    ("total", "total_cents"),
    ("po_number", "po_number"),
    ("direction", "direction"),
    ("claim_frequency_code", "claim_frequency_code"),
    ("document_sha256", "document_sha256"),
]
LINE_FIELDS = [
    ("patient_cluster", "patient_cluster"),
    ("dos", "dos"),
    ("code", "code"),
    ("modifiers", "modifiers"),
    ("units", "units"),
    ("charge", "charge_cents"),
    ("rendering_npi", "rendering_npi"),
    ("invoice_number", "invoice_number"),
    ("invoice_date", "invoice_date"),
    ("party", "party"),
    ("claim_frequency_code", "claim_frequency_code"),
    ("visit", "visit"),
    ("description", "description"),
]


def _fmt(v: Any) -> Any:
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _view_invoice(inv: dict[str, Any], disp: dict[str, Any]) -> dict[str, Any]:
    return {
        "party": disp.get("party_name"),
        "invoice_number": disp.get("invoice_number_raw") or inv.get("num_norm"),
        "invoice_date": _fmt(inv.get("inv_date")),
        "total_cents": inv.get("total"),
        "po_number": inv.get("po_number"),
        "direction": inv.get("direction"),
        "claim_frequency_code": inv.get("freq"),
        "document_sha256": (inv.get("doc_sha") or "")[:12] or None,
    }


def _view_line(line: dict[str, Any], inv: dict[str, Any], disp: dict[str, Any]) -> dict[str, Any]:
    return {
        "patient_cluster": f"P-{line['patient_cluster']}" if line.get("patient_cluster") else None,
        "dos": _fmt(line.get("dos")),
        "code": line.get("code"),
        "modifiers": line.get("mods") or "",
        "units": _fmt(line.get("units")),
        "charge_cents": line.get("charge"),
        "rendering_npi": line.get("npi"),
        "invoice_number": disp.get("invoice_number_raw") or inv.get("num_norm"),
        "invoice_date": _fmt(inv.get("inv_date")),
        "party": disp.get("party_name"),
        "claim_frequency_code": inv.get("freq"),
        "visit": line.get("visit"),
        "description": line.get("desc_norm") or None,
    }


def build_evidence(
    c: Candidate,
    rule: Rule,
    invoices: dict[int, dict[str, Any]],
    lines: dict[int, dict[str, Any]],
    display: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if c.subject_type == "INVOICE":
        s_inv = invoices[c.subject_id]
        a = _view_invoice(s_inv, display.get(c.subject_id, {}))
        cps = [invoices[i] for i in c.counterpart_ids if i in invoices]
        bs = [_view_invoice(ci, display.get(ci["id"], {})) for ci in cps]
        fields = INVOICE_FIELDS
        amount = int(s_inv.get("total") or 0)
        other_invoice_ids = [ci["id"] for ci in cps]
    else:
        sl = lines[c.subject_id]
        s_inv = invoices[sl["invoice_id"]]
        a = _view_line(sl, s_inv, display.get(s_inv["id"], {}))
        cls = [lines[i] for i in c.counterpart_ids if i in lines]
        bs = [_view_line(cl, invoices[cl["invoice_id"]], display.get(cl["invoice_id"], {})) for cl in cls]
        fields = LINE_FIELDS
        amount = int(sl.get("charge") or 0)
        other_invoice_ids = [cl["invoice_id"] for cl in cls]
    b = bs[0] if bs else {}
    matched: list[dict[str, Any]] = []
    differing: list[dict[str, Any]] = []
    for name, key in fields:
        av, bv = a.get(key), b.get(key) if b else None
        method = c.methods.get(name)
        if not b:
            continue
        if method is not None or (av == bv and av not in (None, "")):
            entry: dict[str, Any] = {"field": name, "a": av, "b": bv}
            if isinstance(method, dict):
                entry.update(method)
            else:
                entry["method"] = method or "exact"
            (matched if (av == bv or method is not None) else differing).append(entry)
        elif av != bv:
            differing.append({"field": name, "a": av, "b": bv})
    params = {
        "code": a.get("code"),
        "party": a.get("party"),
        "invoice_number": a.get("invoice_number"),
        "total": format_cents(int(a.get("total_cents") or 0)) if c.subject_type == "INVOICE" else None,
        "other_invoice": b.get("invoice_number") if b else None,
        "dos": a.get("dos"),
        **c.summary_params,
    }
    try:
        summary = rule.summary_template.format(**{k: ("" if v is None else v) for k, v in params.items()})
    except (KeyError, IndexError, ValueError):
        summary = rule.title
    return {
        "rule_id": c.rule_id,
        "rule_version": rule.version,
        "title": rule.title,
        "summary_template": rule.summary_template,
        "summary": summary,
        "subject": {"type": c.subject_type, "id": c.subject_id, "view": a},
        "counterparts": [
            {"id": cid, "invoice_id": iid, "view": v}
            for cid, iid, v in zip(c.counterpart_ids, other_invoice_ids, bs, strict=False)
        ],
        "matched_fields": matched,
        "differing_fields": differing,
        "refdata": c.refdata,
        "suppressions_considered": c.suppressions_considered,
        "suppressed_by": c.suppressed_by,
        "downgraded_by": c.downgraded_by,
        "base_tier": c.base_tier,
        "tier": c.tier,
        "extra": {k: v for k, v in c.extra.items() if isinstance(v, (int, float, str, bool, list))},
        "amount_at_risk_cents": amount,
    }
