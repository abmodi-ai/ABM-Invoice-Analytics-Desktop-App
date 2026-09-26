"""T3 Ask: natural language -> SQL, validated before execution.

Defence in depth:
 1. sqlglot parse: exactly one statement, SELECT only (CTEs/UNION allowed), whitelisted views only,
    allow-listed functions only, LIMIT enforced (<= 500).
 2. Execution on a separate read-only connection (mode=ro, query_only) with a SQLite authorizer that
    allows only SELECT/READ/FUNCTION on allow-listed functions, and a progress-handler timeout.
 3. The generated SQL is always shown to the user.
"""

from __future__ import annotations

import json
import time
from typing import Any

import sqlcipher3
import sqlglot
from sqlglot import exp

from invoice_analytics.ai.guard import store_suggestion
from invoice_analytics.ai.service import get_client, load_prompt
from invoice_analytics.context import Engine

_OK: int = int(sqlcipher3.SQLITE_OK)
_DENY: int = int(sqlcipher3.SQLITE_DENY)

VIEWS: dict[str, list[str]] = {
    "v_invoices": [
        "invoice_id",
        "direction",
        "party_name",
        "party_type",
        "party_cluster",
        "invoice_number",
        "invoice_date",
        "due_date",
        "total_cents",
        "currency",
        "po_number",
        "claim_type",
        "claim_frequency_code",
        "status",
        "is_credit",
        "line_count",
    ],
    "v_invoice_lines": [
        "line_id",
        "invoice_id",
        "line_no",
        "patient_cluster",
        "dos_from",
        "dos_to",
        "code_system",
        "code",
        "modifiers",
        "units",
        "charge_cents",
        "allowed_cents",
        "paid_cents",
        "rendering_npi",
        "place_of_service",
        "description",
    ],
    "v_flags": [
        "flag_id",
        "rule_id",
        "tier",
        "score",
        "status",
        "subject_type",
        "invoice_id",
        "party_id",
        "amount_at_risk_cents",
        "suppressed_by",
        "created_at",
    ],
    "v_reviews": ["review_id", "flag_id", "decision", "reason_code", "decided_at", "recovered_cents", "reviewer"],
    "v_parties": ["party_id", "party_type", "display_name", "cluster_id", "npi", "active"],
}
MAX_LIMIT = 500
TIMEOUT_S = 5.0
ALLOWED_FUNCTIONS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "total",
    "group_concat",
    "abs",
    "round",
    "coalesce",
    "ifnull",
    "nullif",
    "lower",
    "upper",
    "length",
    "substr",
    "substring",
    "trim",
    "ltrim",
    "rtrim",
    "replace",
    "instr",
    "date",
    "datetime",
    "strftime",
    "julianday",
    "cast",
    "iif",
    "like",
    "glob",
    "printf",
    "json_extract",
    "json_array_length",
}

SCHEMA_OUT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sql", "explanation"],
    "properties": {"sql": {"type": "string", "maxLength": 4000}, "explanation": {"type": "string", "maxLength": 400}},
}


class UnsafeSQL(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Return a safe, LIMIT-enforced SQL string or raise UnsafeSQL."""
    if not sql or len(sql) > 4000:
        raise UnsafeSQL("empty or oversized query")
    try:
        stmts = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"could not parse SQL: {str(e)[:120]}") from e
    if len(stmts) != 1:
        raise UnsafeSQL("exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeSQL("only SELECT statements are allowed")
    for bad in (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
        exp.Pragma,
        exp.Attach,
        exp.Detach,
        exp.Transaction,
        exp.Commit,
        exp.Rollback,
    ):
        if tree.find(bad) is not None:
            raise UnsafeSQL(f"{bad.__name__.upper()} is not allowed")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if t.args.get("db") or t.args.get("catalog"):
            raise UnsafeSQL("schema-qualified tables are not allowed")
        if name not in VIEWS and name not in ctes:
            raise UnsafeSQL(f"table {t.name!r} is not an allowed view")
    for f in tree.find_all(exp.Func):
        fname = (f.sql_name() if not isinstance(f, exp.Anonymous) else f.name).lower()
        if isinstance(
            f,
            (
                exp.Cast,
                exp.Count,
                exp.Sum,
                exp.Avg,
                exp.Min,
                exp.Max,
                exp.Coalesce,
                exp.Case,
                exp.If,
                exp.Abs,
                exp.Round,
                exp.Lower,
                exp.Upper,
                exp.Length,
                exp.Substring,
                exp.Trim,
            ),
        ):
            continue
        if fname not in ALLOWED_FUNCTIONS:
            raise UnsafeSQL(f"function {fname}() is not allowed")
    # enforce LIMIT on the outermost query
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(MAX_LIMIT)
    else:
        try:
            n = int(limit.expression.name)
        except (AttributeError, ValueError):
            n = MAX_LIMIT + 1
        if n > MAX_LIMIT:
            tree = tree.limit(MAX_LIMIT)
    return tree.sql(dialect="sqlite")


_RO_OK = {
    sqlcipher3.SQLITE_SELECT,
    sqlcipher3.SQLITE_READ,
    sqlcipher3.SQLITE_FUNCTION,
    getattr(sqlcipher3, "SQLITE_RECURSIVE", 33),
}


SENSITIVE_COLUMNS = {
    "first_name_enc",
    "last_name_enc",
    "patient_key",
    "first_phonetic_hmac",
    "last_phonetic_hmac",
    "tax_id_hmac",
    "value_hmac",
    "password_hash",
    "description_embedding",
    "before",
    "after",
}


def _ro_authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, src: str | None) -> int:
    if action == sqlcipher3.SQLITE_READ and (arg2 or "").lower() in SENSITIVE_COLUMNS:
        return _DENY
    if action == sqlcipher3.SQLITE_FUNCTION:
        name = (arg2 or "").lower()
        return _OK if name in ALLOWED_FUNCTIONS or name in ("json_extract",) else _DENY
    if action in _RO_OK:
        return _OK
    return _DENY


def run_readonly(eng: Engine, sql: str) -> dict[str, Any]:
    safe = validate_sql(sql)
    conn = eng.db.open_readonly()
    try:
        conn.set_authorizer(_ro_authorizer)
        deadline = time.monotonic() + TIMEOUT_S
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        t0 = time.perf_counter()
        try:
            cur = conn.execute(safe)
            cols = [d[0] for d in cur.description]
            rows = [list(r.values()) if isinstance(r, dict) else list(r) for r in cur.fetchmany(MAX_LIMIT)]
        except sqlcipher3.OperationalError as e:
            if "interrupted" in str(e):
                raise UnsafeSQL("query timed out") from e
            raise UnsafeSQL(f"query failed: {str(e)[:160]}") from e
        except sqlcipher3.DatabaseError as e:
            raise UnsafeSQL(f"query rejected: {str(e)[:160]}") from e
        return {
            "sql": safe,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "ms": int((time.perf_counter() - t0) * 1000),
        }
    finally:
        conn.close()


def schema_text() -> str:
    return "; ".join(f"{v}({', '.join(cols)})" for v, cols in VIEWS.items())


def ask(eng: Engine, question: str, user_id: int | None = None) -> dict[str, Any]:
    system, version = load_prompt("nlq")
    system = system.replace("{schema}", schema_text())
    client = get_client(eng)
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question[:1000]}]
    status, output, result, error = "OK", None, None, None
    try:
        res = client.chat(messages, json_schema=SCHEMA_OUT, max_tokens=500, timeout=60)
        output = json.loads(res.content or "{}")
        result = run_readonly(eng, str(output.get("sql", "")))
        output["sql_executed"] = result["sql"]
    except UnsafeSQL as e:
        status, error = "INVALID", str(e)
    except (json.JSONDecodeError, KeyError):
        status, error = "INVALID", "model output did not match the schema"
    latency = int((time.perf_counter() - t0) * 1000)
    sid = store_suggestion(
        eng,
        target_type="QUESTION",
        target_id=user_id,
        task="NLQ",
        model_id=client.model_id,
        model_sha256=client.model_sha256,
        prompt_version=version,
        input_obj={"q": question},
        output={
            "generated": output,
            "error": error,
            "columns": result["columns"] if result else None,
            "rows": result["rows"][:100] if result else None,
            "row_count": result["row_count"] if result else 0,
        },
        trace={"messages": messages},
        latency_ms=latency,
        status=status,
    )
    return {"suggestion_id": sid, "status": status, "error": error, "generated": output, "result": result}
