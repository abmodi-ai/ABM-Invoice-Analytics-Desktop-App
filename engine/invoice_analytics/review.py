"""Review workflow: confirm / dismiss / needs info. Every decision is audited and becomes a label."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.scoring.pipeline import review_pair_key
from invoice_analytics.security import audit

DECISIONS = {"CONFIRMED_DUPLICATE": "CONFIRMED", "NOT_DUPLICATE": "DISMISSED", "NEEDS_INFO": "NEEDS_INFO"}
REASON_CODES = {
    "CONFIRMED_DUPLICATE": [
        "EXACT_RESUBMISSION",
        "RENUMBERED_REBILL",
        "OCR_VARIANT",
        "SPLIT_BILLING",
        "UNITS_OVER_LIMIT",
        "UNBUNDLED",
        "GLOBAL_PERIOD",
        "OTHER",
    ],
    "NOT_DUPLICATE": [
        "DIFFERENT_SERVICE",
        "LEGITIMATE_REPEAT",
        "RECURRING_SERIES",
        "CORRECTED_CLAIM",
        "CREDITED",
        "DIFFERENT_PATIENT",
        "DATA_ERROR",
        "OTHER",
    ],
    "NEEDS_INFO": ["NEED_DOCUMENTATION", "NEED_VENDOR_CONFIRMATION", "NEED_CODER_REVIEW", "OTHER"],
}


class ReviewError(ValueError):
    pass


def siblings(c: Any, flag: dict[str, Any]) -> list[dict[str, Any]]:
    """Other open flags on the same subject invoice against the same counterpart invoices: the same
    duplicate seen by another rule (e.g. INV-001 + INV-006 + CLN-001 on one resubmitted invoice)."""
    return list(
        c.execute(
            "SELECT * FROM flags WHERE subject_invoice_id=? AND counterpart_invoice_ids=? AND id<>?"
            " AND status IN ('OPEN','NEEDS_INFO') AND active=1",
            (flag["subject_invoice_id"], flag["counterpart_invoice_ids"], flag["id"]),
        ).fetchall()
    )


def decide(
    eng: Engine,
    flag_id: int,
    decision: str,
    user_id: int,
    *,
    reason_code: str | None = None,
    note: str | None = None,
    recovered_cents: int | None = None,
    apply_to_siblings: bool = True,
) -> dict[str, Any]:
    """Record a decision. By default it also applies to sibling flags (same invoice pair, other
    rules) so one duplicate is decided once; each sibling gets its own review row and audit entry.
    Recovered money is recorded on the decided flag only."""
    if decision not in DECISIONS:
        raise ReviewError(f"invalid decision {decision}")
    if reason_code and reason_code not in REASON_CODES[decision]:
        raise ReviewError(f"invalid reason code {reason_code} for {decision}")
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    new_status = DECISIONS[decision]
    applied: list[int] = []
    with eng.db.tx() as c:
        flag = c.execute("SELECT * FROM flags WHERE id=?", (flag_id,)).fetchone()
        if flag is None:
            raise ReviewError("flag not found")
        targets = [(flag, recovered_cents, note)]
        if apply_to_siblings:
            targets += [
                (sib, None, f"applied from flag {flag_id}" + (f": {note}" if note else "")) for sib in siblings(c, flag)
            ]
        rid = 0
        for f, rec, n in targets:
            cur = c.execute(
                "INSERT INTO reviews(flag_id, reviewer_user_id, decision, reason_code, note, pair_key, decided_at,"
                " recovered_cents) VALUES (?,?,?,?,?,?,?,?)",
                (f["id"], user_id, decision, reason_code, n, review_pair_key(f), ts, rec),
            )
            if f["id"] == flag_id:
                rid = int(cur.lastrowid)
            else:
                applied.append(f["id"])
            c.execute("UPDATE flags SET status=?, updated_at=? WHERE id=?", (new_status, ts, f["id"]))
            audit.record(
                eng.db,
                "REVIEW_DECISION",
                user_id=user_id,
                entity_type="flag",
                entity_id=f["id"],
                before={"status": f["status"]},
                after={
                    "status": new_status,
                    "decision": decision,
                    "reason_code": reason_code,
                    "recovered_cents": rec,
                    "via_flag": None if f["id"] == flag_id else flag_id,
                },
                conn=c,
            )
    return {"review_id": rid, "flag_id": flag_id, "status": new_status, "applied_to": applied}


def history(eng: Engine, flag_id: int) -> list[dict[str, Any]]:
    return eng.db.query(
        "SELECT r.*, u.display_name AS reviewer FROM reviews r LEFT JOIN users u ON u.id=r.reviewer_user_id"
        " WHERE r.flag_id=? ORDER BY r.decided_at",
        (flag_id,),
    )
