"""The AI layer proposes and never decides.

AI code gets its own SQLCipher connection with a SQLite authorizer that denies every write except
to ai_suggestions and jobs. A bug or prompt-injected tool call cannot touch flags, reviews,
invoices or the audit log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlcipher3

from verismo_engine.context import Engine
from verismo_engine.security.crypto import canonical_json, sha256_hex

_OK: int = int(sqlcipher3.SQLITE_OK)
_DENY: int = int(sqlcipher3.SQLITE_DENY)

AI_WRITABLE = {"ai_suggestions", "jobs"}
_WRITE_OPS = {
    sqlcipher3.SQLITE_INSERT,
    sqlcipher3.SQLITE_UPDATE,
    sqlcipher3.SQLITE_DELETE,
    sqlcipher3.SQLITE_CREATE_TABLE,
    sqlcipher3.SQLITE_DROP_TABLE,
    sqlcipher3.SQLITE_ALTER_TABLE,
    sqlcipher3.SQLITE_CREATE_INDEX,
    sqlcipher3.SQLITE_DROP_INDEX,
    sqlcipher3.SQLITE_CREATE_TRIGGER,
    sqlcipher3.SQLITE_DROP_TRIGGER,
    sqlcipher3.SQLITE_CREATE_VIEW,
    sqlcipher3.SQLITE_DROP_VIEW,
    sqlcipher3.SQLITE_ATTACH,
    sqlcipher3.SQLITE_DETACH,
}


def _authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, src: str | None) -> int:
    if action == sqlcipher3.SQLITE_PRAGMA:
        return (
            _OK
            if (arg1 or "").lower()
            in (
                "key",
                "cipher_memory_security",
                "foreign_keys",
                "busy_timeout",
                "temp_store",
                "query_only",
                "journal_mode",
                "synchronous",
            )
            else _DENY
        )
    if action in _WRITE_OPS:
        if (
            action in (sqlcipher3.SQLITE_INSERT, sqlcipher3.SQLITE_UPDATE, sqlcipher3.SQLITE_DELETE)
            and (arg1 or "") in AI_WRITABLE
        ):
            return _OK
        if action == sqlcipher3.SQLITE_INSERT and (arg1 or "").startswith("sqlite_"):
            return _OK
        return _DENY
    return _OK


def ai_connection(eng: Engine) -> Any:
    conn = eng.db._open()  # noqa: SLF001 - deliberate: a separate, restricted connection
    conn.set_authorizer(_authorizer)
    return conn


def store_suggestion(
    eng: Engine,
    *,
    target_type: str,
    target_id: int | None,
    task: str,
    model_id: str,
    model_sha256: str | None,
    prompt_version: str,
    input_obj: Any,
    output: Any,
    trace: Any,
    latency_ms: int,
    status: str,
) -> int:
    conn = ai_connection(eng)
    try:
        cur = conn.execute(
            "INSERT INTO ai_suggestions(target_type, target_id, task, model_id, model_sha256, prompt_version,"
            " input_hash, output, trace, latency_ms, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                target_type,
                target_id,
                task,
                model_id,
                model_sha256,
                prompt_version,
                sha256_hex(canonical_json(input_obj)),
                canonical_json(output) if output is not None else None,
                canonical_json(trace) if trace is not None else None,
                latency_ms,
                status,
                datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()
