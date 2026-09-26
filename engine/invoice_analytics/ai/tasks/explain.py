"""T2 Explain: restate a flag's evidence in plain language. Output is post-checked: every code,
amount and date in the text must appear in the evidence, otherwise it is discarded (INVALID)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from invoice_analytics.ai.guard import store_suggestion
from invoice_analytics.ai.runtime import LLMTimeout
from invoice_analytics.ai.service import get_client, load_prompt
from invoice_analytics.context import Engine
from invoice_analytics.normalize import format_cents

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["explanation", "key_differences"],
    "properties": {
        "explanation": {"type": "string", "maxLength": 1200},
        "key_differences": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
    },
}

_CODE = re.compile(r"\b(\d{4}[0-9A-Z]|[A-V]\d{4})\b")
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?")


def evidence_facts(evidence: dict[str, Any]) -> set[str]:
    """All atomic values present anywhere in the evidence, plus dollar renderings of cents."""
    facts: set[str] = set()

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif v is not None:
            s = str(v)
            facts.add(s.upper())
            for m in _CODE.findall(s.upper()):
                facts.add(m)
            for m in _ISO.findall(s):
                facts.add(m)
            if isinstance(v, int) and not isinstance(v, bool) and ("cents" in key or key in ("a", "b")):
                facts.add(format_cents(v))
                facts.add(format_cents(abs(v)))
            for m in _MONEY.finditer(s):
                facts.add(_norm_money(m))

    walk(evidence)
    return facts


def _norm_money(m: re.Match[str]) -> str:
    whole = int(m.group(1).replace(",", ""))
    cents = int(m.group(2) or 0)
    return format_cents(whole * 100 + cents)


def post_check(text: str, evidence: dict[str, Any]) -> list[str]:
    """Return the list of unsupported facts (empty = passes)."""
    facts = evidence_facts(evidence)
    bad: list[str] = []
    for c in _CODE.findall(text.upper()):
        if c not in facts:
            bad.append(f"code {c}")
    for d in _ISO.findall(text):
        if d not in facts:
            bad.append(f"date {d}")
    for m in _US_DATE.finditer(text):
        iso = f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        if iso not in facts:
            bad.append(f"date {m.group(0)}")
    for m in _MONEY.finditer(text):
        if _norm_money(m) not in facts:
            bad.append(f"amount {m.group(0)}")
    return bad


def explain_flag(eng: Engine, flag_id: int) -> dict[str, Any]:
    flag = eng.db.one("SELECT id, evidence FROM flags WHERE id=?", (flag_id,))
    if flag is None:
        raise ValueError("flag not found")
    evidence = json.loads(flag["evidence"])
    # The model sees evidence only: no free-form record access.
    ev_for_model = {
        k: evidence[k]
        for k in (
            "rule_id",
            "title",
            "summary",
            "subject",
            "counterparts",
            "matched_fields",
            "differing_fields",
            "refdata",
            "suppressions_considered",
            "tier",
            "amount_at_risk_cents",
        )
        if k in evidence
    }
    system, version = load_prompt("explain")
    client = get_client(eng)
    t0 = time.perf_counter()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "EVIDENCE:\n" + json.dumps(ev_for_model, indent=1, default=str)},
    ]
    status, output, problems = "OK", None, []
    try:
        res = client.chat(messages, json_schema=SCHEMA, max_tokens=600, timeout=60)
        output = json.loads(res.content or "{}")
        if not isinstance(output.get("explanation"), str) or not isinstance(output.get("key_differences"), list):
            raise ValueError("schema")
        problems = post_check(output["explanation"] + " " + " ".join(output["key_differences"]), ev_for_model)
        if problems:
            status = "INVALID"
    except (json.JSONDecodeError, ValueError, KeyError):
        status = "INVALID"
        problems = ["output did not match the schema"]
    except LLMTimeout:
        status = "TIMEOUT"
    latency = int((time.perf_counter() - t0) * 1000)
    sid = store_suggestion(
        eng,
        target_type="FLAG",
        target_id=flag_id,
        task="EXPLAIN",
        model_id=client.model_id,
        model_sha256=client.model_sha256,
        prompt_version=version,
        input_obj=ev_for_model,
        output=output,
        trace={"messages": messages, "post_check_failures": problems},
        latency_ms=latency,
        status=status,
    )
    return {"suggestion_id": sid, "status": status, "problems": problems}
