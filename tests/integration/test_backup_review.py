"""Restore round trip, pre-migration backup, sibling decisions and per-invoice money."""

from __future__ import annotations

from pathlib import Path

from verismo_engine.backup import create_backup, restore_backup
from verismo_engine.context import Engine, open_engine
from verismo_engine.reports import dashboard
from verismo_engine.review import decide
from verismo_engine.security import audit
from verismo_engine.security.users import create_user

from ..conftest import make_config
from .test_ingest_detect import ingest, row


def _dup_pair(eng: Engine) -> None:
    ingest(
        eng,
        "a.csv",
        row("Acme", "INV-0045", "2026-03-10", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"),
    )
    ingest(
        eng,
        "b.csv",
        row("Acme", "INV-0045", "2026-04-02", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"),
    )


def test_restore_roundtrip(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    eng = open_engine(cfg)
    _dup_pair(eng)
    n_inv = eng.db.scalar("SELECT COUNT(*) FROM invoices")
    n_flags = eng.db.scalar("SELECT COUNT(*) FROM flags")
    bk = create_backup(eng, tmp_path / "bk", user_id=None, recovery_key="AAAAA-BBBBB")
    # mutate after the backup
    ingest(
        eng, "c.csv", row("Other", "999", "2026-05-01", "10.00", 1, "Ann", "Lee", "2026-04-01", "99213", charge="10.00")
    )
    assert eng.db.scalar("SELECT COUNT(*) FROM invoices") == n_inv + 1
    res = restore_backup(eng, bk, user_id=None)
    assert res["previous_db"].endswith(".pre-restore")
    eng2 = open_engine(cfg)
    try:
        assert eng2.db.scalar("SELECT COUNT(*) FROM invoices") == n_inv
        assert eng2.db.scalar("SELECT COUNT(*) FROM flags") == n_flags
        assert audit.verify(eng2.db).ok
        # the restored engine keeps working
        _dup_pair(eng2)
        assert eng2.db.scalar("SELECT COUNT(*) FROM invoices") == n_inv + 2
    finally:
        eng2.close()


def test_restore_on_new_install_with_recovery_key(tmp_path: Path) -> None:
    src = open_engine(make_config(tmp_path / "old"))
    _dup_pair(src)
    bk = create_backup(src, tmp_path / "bk", user_id=None, recovery_key="RECOV-ERYKE-Y0001")
    src.close()
    fresh_cfg = make_config(tmp_path / "new")
    fresh = open_engine(fresh_cfg)  # different install keys
    restore_backup(fresh, bk, user_id=None, recovery_key="RECOV-ERYKE-Y0001")
    again = open_engine(fresh_cfg)
    try:
        assert again.db.scalar("SELECT COUNT(*) FROM invoices") == 2
        p = again.db.one("SELECT last_name_enc FROM patients")
        assert again.field_cipher.decrypt(p["last_name_enc"], aad="patient.last") == "GARCIA"  # keys travelled
    finally:
        again.close()


def test_pre_migration_backup(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    eng = open_engine(cfg)
    eng.db.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")
    eng.db.execute("DROP INDEX IF EXISTS ix_flags_subject")
    eng.db.conn.commit()
    eng.close()
    eng2 = open_engine(cfg)  # sees one pending migration
    try:
        backups = list((cfg.data_dir / "backups").glob("pre-migrate-*.db"))
        assert len(backups) == 1 and backups[0].stat().st_size > 0
        assert b"SQLite format 3" not in backups[0].read_bytes()[:16]  # still encrypted
    finally:
        eng2.close()


def test_decision_applies_to_sibling_flags_and_money_counts_once(engine: Engine) -> None:
    _dup_pair(engine)
    uid = create_user(engine.db, "rev", "Reviewer", "REVIEWER", "reviewer password")
    open_before = engine.db.query(
        "SELECT id, rule_id FROM flags WHERE status='OPEN' AND suppressed_by IS NULL AND active=1"
    )
    assert len(open_before) >= 2  # e.g. INV-001 + CLN-001 (+ CLN-004/INV-006)
    d = dashboard(engine)
    assert d["open_by_tier"]["HARD"]["count"] == 1  # one duplicate invoice, not one per rule
    assert d["amount_at_risk_cents"] == 12500  # counted once
    first = next(f for f in open_before if f["rule_id"] == "INV-001")
    res = decide(
        engine, first["id"], "CONFIRMED_DUPLICATE", uid, reason_code="EXACT_RESUBMISSION", recovered_cents=12500
    )
    assert len(res["applied_to"]) >= 1
    assert (
        engine.db.scalar(
            "SELECT COUNT(*) FROM flags WHERE status='OPEN' AND active=1 AND suppressed_by IS NULL"
            " AND tier IN ('HARD','PROBABLE')"
        )
        == 0
    )
    assert engine.db.scalar("SELECT SUM(recovered_cents) FROM reviews") == 12500
    assert dashboard(engine)["amount_recovered_cents"] == 12500
