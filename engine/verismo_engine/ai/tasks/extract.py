"""T1 Extract: LLM extraction for low-confidence documents. Schema-constrained; the result is an
AI suggestion attached to the draft, never an invoice. A person accepts or corrects it."""

from __future__ import annotations

import json
import time
from typing import Any

from verismo_engine.ai.guard import store_suggestion
from verismo_engine.ai.service import get_client, load_prompt
from verismo_engine.context import Engine
from verismo_engine.ingest.pdf.pipeline import score_draft
from verismo_engine.normalize import AmountError, parse_amount

_FIELD = {"type": ["string", "null"], "maxLength": 200}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["invoice", "lines", "confidence"],
    "properties": {
        "invoice": {
            "type": "object",
            "additionalProperties": False,
            "required": ["vendor_name", "invoice_number", "invoice_date", "total"],
            "properties": {
                k: _FIELD
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
        },
        "lines": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "amount"],
                "properties": {
                    "description": _FIELD,
                    "code": _FIELD,
                    "units": _FIELD,
                    "amount": _FIELD,
                    "dos": _FIELD,
                    "modifiers": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 2}},
                },
            },
        },
        "confidence": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}},
    },
}


def arithmetic_ok(out: dict[str, Any]) -> bool:
    try:
        total = parse_amount(out["invoice"]["total"])
        return sum(parse_amount(li["amount"]) for li in out["lines"]) == total and bool(out["lines"])
    except (AmountError, KeyError, TypeError):
        return False


def extract_draft(eng: Engine, draft_id: int) -> dict[str, Any]:
    d = eng.db.one("SELECT * FROM extraction_drafts WHERE id=?", (draft_id,))
    if d is None:
        raise ValueError("draft not found")
    draft = json.loads(d["fields"])
    text = draft.get("text", "")[:12000]
    system, version = load_prompt("extract")
    client = get_client(eng)
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "DOCUMENT TEXT:\n" + text}]
    status, out, checks = "OK", None, {}
    try:
        res = client.chat(messages, json_schema=SCHEMA, max_tokens=2000, timeout=110)
        out = json.loads(res.content or "{}")
        ok = arithmetic_ok(out)
        sc = score_draft({**out["invoice"], "lines": out["lines"], "confidence": out.get("confidence", {})}, 1.0)
        checks = {"arithmetic_ok": ok, "overall": sc["overall"]}
        # values that do not appear in the document text are marked low-confidence for a person to check
        missing = [k for k, v in out["invoice"].items() if v and str(v) not in text]
        checks["not_in_text"] = missing
        if missing:
            for k in missing:
                out.setdefault("confidence", {})[k] = 0.0
    except (json.JSONDecodeError, KeyError, TypeError):
        status = "INVALID"
    latency = int((time.perf_counter() - t0) * 1000)
    sid = store_suggestion(
        eng,
        target_type="EXTRACTION_DRAFT",
        target_id=draft_id,
        task="EXTRACT",
        model_id=client.model_id,
        model_sha256=client.model_sha256,
        prompt_version=version,
        input_obj={"draft_id": draft_id, "text_sha": text[:64]},
        output={"extraction": out, "checks": checks},
        trace={"messages_chars": len(text)},
        latency_ms=latency,
        status=status,
    )
    return {"suggestion_id": sid, "status": status, "checks": checks}
