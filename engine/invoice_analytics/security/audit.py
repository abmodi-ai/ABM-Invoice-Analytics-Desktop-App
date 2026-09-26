"""Append-only, hash-chained audit log.

hash = SHA256(prev_hash || canonical_json(row)) where row excludes id/prev_hash/hash.
UPDATE and DELETE are blocked by triggers; verify() recomputes the whole chain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from invoice_analytics.db.connection import Database
from invoice_analytics.security.crypto import canonical_json, sha256_hex

GENESIS = "0" * 64


def _row_payload(
    ts: str,
    user_id: int | None,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    before: str | None,
    after: str | None,
) -> str:
    return canonical_json(
        {
            "ts": ts,
            "user_id": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": before,
            "after": after,
        }
    )


def _dump(v: Any) -> str | None:
    if v is None:
        return None
    return canonical_json(v)


def record(
    db: Database,
    action: str,
    *,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    before: Any = None,
    after: Any = None,
    conn: Any = None,
) -> str:
    """Append an audit event. Pass `conn` when already inside db.tx()."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    eid = None if entity_id is None else str(entity_id)
    b, a = _dump(before), _dump(after)

    def _write(c: Any) -> str:
        prev = c.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = prev["hash"] if prev else GENESIS
        h = sha256_hex(prev_hash + _row_payload(ts, user_id, action, entity_type, eid, b, a))
        c.execute(
            "INSERT INTO audit_log(ts,user_id,action,entity_type,entity_id,before,after,prev_hash,hash)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, user_id, action, entity_type, eid, b, a, prev_hash, h),
        )
        return h

    if conn is not None:
        return _write(conn)
    with db.tx() as c:
        return _write(c)


@dataclass
class VerifyResult:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checked": self.checked, "first_bad_id": self.first_bad_id, "reason": self.reason}


def verify(db: Database) -> VerifyResult:
    prev_hash = GENESIS
    n = 0
    last_id = 0
    for row in db.conn.execute("SELECT * FROM audit_log ORDER BY id"):
        n += 1
        if row["id"] != last_id + 1:
            return VerifyResult(False, n, row["id"], "gap in audit ids (row removed)")
        last_id = row["id"]
        if row["prev_hash"] != prev_hash:
            return VerifyResult(False, n, row["id"], "prev_hash does not match previous row")
        expect = sha256_hex(
            prev_hash
            + _row_payload(
                row["ts"],
                row["user_id"],
                row["action"],
                row["entity_type"],
                row["entity_id"],
                row["before"],
                row["after"],
            )
        )
        if expect != row["hash"]:
            return VerifyResult(False, n, row["id"], "row content does not match its hash")
        prev_hash = row["hash"]
    return VerifyResult(True, n)


def decode(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in ("before", "after"):
        if out.get(k):
            out[k] = json.loads(out[k])
    return out
