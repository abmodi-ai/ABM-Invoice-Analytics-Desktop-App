"""Read-only tools for the triage agent (spec 7.4). Each has a Pydantic argument schema and
returns compact JSON. Patient names are never returned: patients appear as cluster ids."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from invoice_analytics.clinical import refdata as rd
from invoice_analytics.context import Engine
from invoice_analytics.normalize import normalize_modifiers


class GetFlag(BaseModel):
    flag_id: int


class GetInvoice(BaseModel):
    invoice_id: int


class SearchInvoices(BaseModel):
    party_id: int | None = None
    patient_cluster: int | None = None
    code: str | None = None
    dos_from: str | None = Field(None, description="YYYY-MM-DD")
    dos_to: str | None = Field(None, description="YYYY-MM-DD")
    amount_cents: int | None = None
    limit: int = Field(10, ge=1, le=20)


class PatientHistory(BaseModel):
    patient_cluster: int
    dos_from: str
    dos_to: str
    code: str | None = None


class PtpLookup(BaseModel):
    code_a: str
    code_b: str
    dos: str


class CodeDos(BaseModel):
    code: str
    dos: str


class CheckModifiers(BaseModel):
    code: str
    modifiers: list[str]


class PriorReviews(BaseModel):
    subject_ids: list[int] = Field(..., max_length=20)


class DiffInvoices(BaseModel):
    invoice_a: int
    invoice_b: int


def _invoice(eng: Engine, invoice_id: int) -> dict[str, Any] | None:
    inv = eng.db.one(
        "SELECT i.id, i.direction, i.party_id, p.display_name AS party, p.cluster_id AS party_cluster,"
        " i.invoice_number_raw AS invoice_number, i.invoice_date, i.total_cents, i.status, i.claim_frequency_code,"
        " i.original_invoice_ref, i.is_credit FROM invoices i JOIN parties p ON p.id=i.party_id WHERE i.id=?",
        (invoice_id,),
    )
    if inv is None:
        return None
    inv["lines"] = eng.db.query(
        "SELECT l.id AS line_id, l.line_no, pt.cluster_id AS patient_cluster, l.dos_from AS dos, l.code, l.modifiers,"
        " l.units, l.charge_cents, l.paid_cents, l.rendering_npi, l.description_norm AS description"
        " FROM invoice_lines l LEFT JOIN patients pt ON pt.id=l.patient_id WHERE l.invoice_id=? ORDER BY l.line_no",
        (invoice_id,),
    )
    for li in inv["lines"]:
        li["modifiers"] = json.loads(li["modifiers"])
    return inv


def get_flag(eng: Engine, a: GetFlag) -> dict[str, Any]:
    f = eng.db.one(
        "SELECT id, rule_id, tier, score, subject_type, subject_id, subject_invoice_id,"
        " counterpart_ids, counterpart_invoice_ids, evidence, status FROM flags WHERE id=?",
        (a.flag_id,),
    )
    if f is None:
        return {"error": "not found"}
    ev = json.loads(f.pop("evidence"))
    f["counterpart_ids"] = json.loads(f["counterpart_ids"])
    f["counterpart_invoice_ids"] = json.loads(f["counterpart_invoice_ids"])
    f["summary"] = ev.get("summary")
    f["matched_fields"] = ev.get("matched_fields")
    f["differing_fields"] = ev.get("differing_fields")
    return f


def get_invoice(eng: Engine, a: GetInvoice) -> dict[str, Any]:
    return _invoice(eng, a.invoice_id) or {"error": "not found"}


def search_invoices(eng: Engine, a: SearchInvoices) -> dict[str, Any]:
    where: list[str] = ["1=1"]
    params: list[Any] = []
    if a.party_id is not None:
        where.append("p.cluster_id=(SELECT cluster_id FROM parties WHERE id=?)")
        params.append(a.party_id)
    if a.amount_cents is not None:
        where.append("i.total_cents=?")
        params.append(a.amount_cents)
    if a.patient_cluster is not None or a.code or a.dos_from or a.dos_to:
        sub = ["1=1"]
        if a.patient_cluster is not None:
            sub.append("pt.cluster_id=?")
            params.append(a.patient_cluster)
        if a.code:
            sub.append("l.code=?")
            params.append(a.code.upper())
        if a.dos_from:
            sub.append("l.dos_from>=?")
            params.append(a.dos_from)
        if a.dos_to:
            sub.append("l.dos_from<=?")
            params.append(a.dos_to)
        where.append(
            "i.id IN (SELECT l.invoice_id FROM invoice_lines l LEFT JOIN patients pt ON pt.id=l.patient_id"
            f" WHERE {' AND '.join(sub)})"
        )
    rows = eng.db.query(
        "SELECT i.id AS invoice_id, p.display_name AS party, i.invoice_number_raw AS invoice_number, i.invoice_date,"
        f" i.total_cents, i.status FROM invoices i JOIN parties p ON p.id=i.party_id WHERE {' AND '.join(where)}"
        " ORDER BY i.invoice_date DESC LIMIT ?",
        [*params, a.limit],
    )
    return {"results": rows, "count": len(rows)}


def get_patient_service_history(eng: Engine, a: PatientHistory) -> dict[str, Any]:
    rows = eng.db.query(
        "SELECT l.id AS line_id, l.invoice_id, i.invoice_number_raw AS invoice_number, l.dos_from AS dos, l.code,"
        " l.modifiers, l.units, l.charge_cents, l.rendering_npi, i.status FROM invoice_lines l"
        " JOIN invoices i ON i.id=l.invoice_id JOIN patients pt ON pt.id=l.patient_id"
        " WHERE pt.cluster_id=? AND l.dos_from BETWEEN ? AND ? AND (? IS NULL OR l.code=?)"
        " ORDER BY l.dos_from LIMIT 50",
        (a.patient_cluster, a.dos_from, a.dos_to, a.code, a.code),
    )
    for r in rows:
        r["modifiers"] = json.loads(r["modifiers"])
    return {"patient_cluster": a.patient_cluster, "services": rows}


def lookup_ncci_ptp(eng: Engine, a: PtpLookup) -> dict[str, Any]:
    return {"edits": rd.lookup_ptp(eng, a.code_a.upper(), a.code_b.upper(), a.dos)}


def lookup_mue(eng: Engine, a: CodeDos) -> dict[str, Any]:
    return {"mue": rd.lookup_mue(eng, a.code.upper(), a.dos)}


def lookup_global_days(eng: Engine, a: CodeDos) -> dict[str, Any]:
    return {"global": rd.lookup_global_days(eng, a.code.upper(), a.dos)}


def check_modifiers(eng: Engine, a: CheckModifiers) -> dict[str, Any]:
    mods = normalize_modifiers(a.modifiers)
    rows = {
        r["modifier"]: r for r in eng.db.query("SELECT modifier, category, ncci_bypass, description FROM ref_modifiers")
    }
    return {
        "code": a.code.upper(),
        "modifiers": [
            {
                "modifier": m,
                "category": rows.get(m, {}).get("category", "UNKNOWN"),
                "ncci_bypass": bool(rows.get(m, {}).get("ncci_bypass")),
                "description": rows.get(m, {}).get("description"),
            }
            for m in mods
        ],
        "any_ncci_bypass": any(rows.get(m, {}).get("ncci_bypass") for m in mods),
    }


def get_prior_reviews(eng: Engine, a: PriorReviews) -> dict[str, Any]:
    if not a.subject_ids:
        return {"reviews": []}
    ph = ",".join("?" * len(a.subject_ids))
    rows = eng.db.query(
        f"SELECT f.rule_id, f.subject_type, f.subject_id, r.decision, r.reason_code, r.decided_at FROM reviews r"
        f" JOIN flags f ON f.id=r.flag_id WHERE f.subject_id IN ({ph}) OR f.subject_invoice_id IN ({ph})"
        f" ORDER BY r.decided_at DESC LIMIT 20",
        [*a.subject_ids, *a.subject_ids],
    )
    return {"reviews": rows}


def diff_invoices(eng: Engine, a: DiffInvoices) -> dict[str, Any]:
    x, y = _invoice(eng, a.invoice_a), _invoice(eng, a.invoice_b)
    if not x or not y:
        return {"error": "not found"}
    head = [
        k
        for k in (
            "party",
            "invoice_number",
            "invoice_date",
            "total_cents",
            "status",
            "claim_frequency_code",
            "original_invoice_ref",
        )
        if x.get(k) != y.get(k)
    ]

    def key(li: dict[str, Any]) -> tuple[Any, Any, Any]:
        return (li["patient_cluster"], li["dos"], li["code"])

    ya = {key(li): li for li in y["lines"]}
    line_diffs = []
    for li in x["lines"]:
        other = ya.get(key(li))
        if other is None:
            line_diffs.append({"line_no_a": li["line_no"], "match": None})
            continue
        diff = {
            k: {"a": li[k], "b": other[k]}
            for k in ("modifiers", "units", "charge_cents", "rendering_npi")
            if li[k] != other[k]
        }
        line_diffs.append(
            {
                "line_no_a": li["line_no"],
                "line_no_b": other["line_no"],
                "code": li["code"],
                "dos": li["dos"],
                "differences": diff,
            }
        )
    return {
        "header_differences": {k: {"a": x.get(k), "b": y.get(k)} for k in head},
        "lines": line_diffs,
        "a_line_count": len(x["lines"]),
        "b_line_count": len(y["lines"]),
    }


TOOLS: dict[str, tuple[type[BaseModel], Callable[[Engine, Any], dict[str, Any]], str]] = {
    "get_flag": (GetFlag, get_flag, "Get a flag's rule, tier, subject, counterparts and evidence summary."),
    "get_invoice": (GetInvoice, get_invoice, "Get an invoice header and its lines."),
    "search_invoices": (
        SearchInvoices,
        search_invoices,
        "Search invoices by party, patient cluster, code, DOS range or amount.",
    ),
    "get_patient_service_history": (
        PatientHistory,
        get_patient_service_history,
        "Services billed for a patient cluster in a DOS range.",
    ),
    "lookup_ncci_ptp": (PtpLookup, lookup_ncci_ptp, "NCCI procedure-to-procedure edits between two codes on a DOS."),
    "lookup_mue": (CodeDos, lookup_mue, "MUE value and adjudication indicator for a code on a DOS."),
    "lookup_global_days": (CodeDos, lookup_global_days, "Global surgical period for a code on a DOS."),
    "check_modifiers": (CheckModifiers, check_modifiers, "Categories and NCCI-bypass eligibility of modifiers."),
    "get_prior_reviews": (PriorReviews, get_prior_reviews, "Previous human decisions on flags for these subject ids."),
    "diff_invoices": (DiffInvoices, diff_invoices, "Field-level diff between two invoices."),
}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": n, "description": d, "parameters": m.model_json_schema()}}
        for n, (m, _f, d) in TOOLS.items()
    ]


def call_tool(eng: Engine, name: str, arguments: str | dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    model, fn, _ = TOOLS[name]
    try:
        args = model.model_validate(json.loads(arguments) if isinstance(arguments, str) else arguments)
    except Exception as e:  # noqa: BLE001
        return {"error": f"invalid arguments: {str(e)[:200]}"}
    return fn(eng, args)
