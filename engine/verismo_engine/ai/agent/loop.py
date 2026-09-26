"""T4 Triage agent: a small, auditable tool-calling loop (spec 7.4).

At most 8 tool calls and a 120 s budget. The final verdict is schema-constrained and every
cited_evidence item must match a value actually returned by a tool call in the trace; otherwise
the verdict becomes UNCERTAIN and the suggestion is stored as INVALID. Nothing here writes to
flags or reviews: results go to ai_suggestions only.
"""

from __future__ import annotations

import json
import time
from typing import Any

from verismo_engine.ai.agent.tools import call_tool, tool_specs
from verismo_engine.ai.guard import store_suggestion
from verismo_engine.ai.runtime import LLMTimeout
from verismo_engine.ai.service import get_client, load_prompt
from verismo_engine.context import Engine

MAX_TOOL_CALLS = 8
TIME_BUDGET_S = 120.0

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "confidence", "rationale", "cited_evidence", "recommended_action"],
    "properties": {
        "verdict": {"type": "string", "enum": ["DUPLICATE", "NOT_DUPLICATE", "UNCERTAIN"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "maxLength": 1500},
        "cited_evidence": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool_call_id", "field", "value"],
                "properties": {
                    "tool_call_id": {"type": "string"},
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                },
            },
        },
        "recommended_action": {"type": "string", "enum": ["BLOCK", "REVIEW", "DISMISS"]},
    },
}


def _values_by_field(obj: Any, out: dict[str, set[str]], key: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _values_by_field(v, out, k)
    elif isinstance(obj, list):
        if key and all(not isinstance(x, (dict, list)) for x in obj):
            out.setdefault(key, set()).add(json.dumps(obj))
            out.setdefault(key, set()).add(",".join(str(x) for x in obj))
        for x in obj:
            _values_by_field(x, out, key)
    else:
        s = "" if obj is None else str(obj)
        out.setdefault(key, set()).add(s)
        if isinstance(obj, float) and obj.is_integer():
            out[key].add(str(int(obj)))


def validate_citations(verdict: dict[str, Any], trace: list[dict[str, Any]]) -> list[str]:
    """Every cited (tool_call_id, field, value) must exist in that tool call's result."""
    results = {t["id"]: t["result"] for t in trace if t.get("type") == "tool"}
    problems: list[str] = []
    cites = verdict.get("cited_evidence") or []
    if verdict.get("verdict") != "UNCERTAIN" and not cites:
        problems.append("verdict has no citations")
    for c in cites:
        tid = c.get("tool_call_id")
        if tid not in results:
            problems.append(f"citation refers to unknown tool call {tid}")
            continue
        vals: dict[str, set[str]] = {}
        _values_by_field(results[tid], vals)
        field = str(c.get("field", "")).split(".")[-1]
        if field not in vals or str(c.get("value")) not in vals[field]:
            problems.append(f"{tid}: {field}={c.get('value')!r} not found in tool result")
    return problems


def triage_flag(eng: Engine, flag_id: int, *, cancelled: Any = None) -> dict[str, Any]:
    flag = eng.db.one("SELECT id, evidence FROM flags WHERE id=?", (flag_id,))
    if flag is None:
        raise ValueError("flag not found")
    evidence = json.loads(flag["evidence"])
    system, version = load_prompt("triage")
    client = get_client(eng)
    user = (
        "FLAG ID: "
        + str(flag_id)
        + "\nEVIDENCE:\n"
        + json.dumps(
            {
                k: evidence.get(k)
                for k in (
                    "rule_id",
                    "summary",
                    "subject",
                    "counterparts",
                    "matched_fields",
                    "differing_fields",
                    "refdata",
                    "tier",
                )
            },
            indent=1,
            default=str,
        )
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    trace: list[dict[str, Any]] = [{"type": "prompt", "system_version": version, "user": user}]
    tools = tool_specs()
    t0 = time.perf_counter()
    calls = 0
    status = "OK"
    verdict: dict[str, Any] | None = None
    try:
        while calls < MAX_TOOL_CALLS:
            if cancelled is not None and cancelled():
                raise TimeoutError("cancelled")
            remaining = TIME_BUDGET_S - (time.perf_counter() - t0)
            if remaining <= 5:
                raise TimeoutError("time budget exhausted")
            res = client.chat(messages, tools=tools, max_tokens=700, timeout=remaining)
            if not res.tool_calls:
                break
            messages.append({"role": "assistant", "content": res.content or "", "tool_calls": res.tool_calls})
            for tc in res.tool_calls:
                if calls >= MAX_TOOL_CALLS:
                    break
                calls += 1
                tid = f"tc_{calls}"
                fn = tc.get("function", {})
                out = call_tool(eng, fn.get("name", ""), fn.get("arguments") or "{}")
                trace.append(
                    {"type": "tool", "id": tid, "name": fn.get("name"), "arguments": fn.get("arguments"), "result": out}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", tid),
                        "content": json.dumps({"tool_call_id": tid, "result": out}, default=str)[:6000],
                    }
                )
        remaining = max(5.0, TIME_BUDGET_S - (time.perf_counter() - t0))
        messages.append(
            {
                "role": "user",
                "content": "Now give your final verdict as JSON. Cite tool_call_id values "
                "exactly as tc_1, tc_2, ... from the tool results.",
            }
        )
        final = client.chat(messages, json_schema=VERDICT_SCHEMA, max_tokens=700, timeout=remaining)
        verdict = json.loads(final.content or "{}")
        trace.append({"type": "final", "content": final.content})
        problems = validate_citations(verdict, trace)
        if problems:
            status = "INVALID"
            verdict = {
                **verdict,
                "verdict": "UNCERTAIN",
                "recommended_action": "REVIEW",
                "validation_failures": problems,
            }
    except (TimeoutError, LLMTimeout) as e:
        status = "TIMEOUT"
        trace.append({"type": "error", "message": str(e)})
    except json.JSONDecodeError:
        status = "INVALID"
        verdict = {"verdict": "UNCERTAIN", "validation_failures": ["final output was not valid JSON"]}
    latency = int((time.perf_counter() - t0) * 1000)
    sid = store_suggestion(
        eng,
        target_type="FLAG",
        target_id=flag_id,
        task="TRIAGE",
        model_id=client.model_id,
        model_sha256=client.model_sha256,
        prompt_version=version,
        input_obj={"flag_id": flag_id, "evidence": evidence},
        output=verdict,
        trace=trace,
        latency_ms=latency,
        status=status,
    )
    return {"suggestion_id": sid, "status": status, "verdict": (verdict or {}).get("verdict"), "tool_calls": calls}
