from __future__ import annotations

import sqlite3

import pytest

from verismo_engine.context import Engine, open_engine
from verismo_engine.db.migrate import _split_statements, current_version
from verismo_engine.security import audit
from verismo_engine.security.crypto import FieldCipher, KeySet, new_recovery_key
from verismo_engine.security.keystore import Keystore
from verismo_engine.security.users import AuthError, SessionManager, create_user

from ..conftest import make_config


def test_db_is_encrypted(engine: Engine) -> None:
    engine.db.execute("INSERT INTO settings(key,value) VALUES ('probe','\"x\"')")
    engine.db.conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(engine.config.db_path).execute("SELECT * FROM settings").fetchall()
    raw = engine.config.db_path.read_bytes()
    assert b"SQLite format 3" not in raw[:16]


def test_migrations_idempotent(engine: Engine) -> None:
    from verismo_engine.db.migrate import migrate

    assert migrate(engine.db) == []
    assert current_version(engine.db) >= 2
    assert engine.db.scalar("SELECT COUNT(*) FROM ref_modifiers") > 40


def test_split_statements_keeps_triggers() -> None:
    sql = "CREATE TABLE a(x);\nCREATE TRIGGER t BEFORE UPDATE ON a\nBEGIN SELECT 1; END;\nSELECT 2;"
    assert len(_split_statements(sql)) == 3


def test_reopen_with_same_keys(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = make_config(tmp_path)
    e1 = open_engine(cfg)
    e1.settings.set("ai.tier", "LITE")
    e1.close()
    e2 = open_engine(cfg)
    assert e2.settings.get("ai.tier") == "LITE"
    assert not e2.first_run
    e2.close()


def test_field_cipher_roundtrip() -> None:
    fc = FieldCipher(KeySet.generate().field_key)
    t = fc.encrypt("GARCIA", aad="last")
    assert t is not None and "GARCIA" not in t
    assert fc.decrypt(t, aad="last") == "GARCIA"
    with pytest.raises(Exception):
        fc.decrypt(t, aad="first")


def test_recovery_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ks = Keystore(tmp_path, "file")
    keys, _ = ks.load_or_create()
    rk = new_recovery_key()
    ks.write_recovery(keys, rk)
    assert ks.recover(rk) == keys
    with pytest.raises(Exception):
        ks.recover(new_recovery_key())


def test_audit_chain_and_tamper_detection(engine: Engine) -> None:
    for i in range(5):
        audit.record(engine.db, "TEST", entity_type="x", entity_id=i, after={"i": i})
    assert audit.verify(engine.db).ok
    with pytest.raises(Exception):
        engine.db.execute("UPDATE audit_log SET action='EVIL' WHERE id=2")
    with pytest.raises(Exception):
        engine.db.execute("DELETE FROM audit_log WHERE id=2")
    # Simulate an attacker who drops the triggers and edits a row.
    engine.db.execute("DROP TRIGGER audit_log_no_update")
    engine.db.execute("UPDATE audit_log SET after='{\"i\":99}' WHERE id=3")
    engine.db.conn.commit()
    res = audit.verify(engine.db)
    assert not res.ok and res.first_bad_id == 3


def test_users_sessions_and_idle_lock(engine: Engine) -> None:
    uid = create_user(engine.db, "admin", "Admin", "ADMIN", "correct horse battery")
    sm = SessionManager(engine.db, idle_seconds=900)
    with pytest.raises(AuthError):
        sm.login("admin", "wrong password!!")
    s = sm.login("admin", "correct horse battery")
    assert sm.require(s.token, "REVIEWER").user_id == uid
    sm.idle_seconds = 0
    import time

    time.sleep(0.01)
    with pytest.raises(AuthError):
        sm.get(s.token)
    actions = [r["action"] for r in engine.db.query("SELECT action FROM audit_log")]
    assert "LOGIN_FAILED" in actions and "LOGIN" in actions


def test_viewer_cannot_act_as_reviewer(engine: Engine) -> None:
    create_user(engine.db, "v", "Viewer", "VIEWER", "viewer password")
    sm = SessionManager(engine.db)
    s = sm.login("v", "viewer password")
    with pytest.raises(PermissionError):
        sm.require(s.token, "REVIEWER")


def test_settings_versioned_and_audited(engine: Engine) -> None:
    v1 = engine.settings.set("rules.INV-005", {"window_days": 30})
    v2 = engine.settings.set("rules.INV-005", {"window_days": 60})
    assert (v1, v2) == (1, 2)
    assert engine.settings.get("rules.INV-005")["window_days"] == 60
    assert engine.settings.get("rules.INV-005")["tier"] == "WEAK"
    assert engine.db.scalar("SELECT COUNT(*) FROM settings_history WHERE key='rules.INV-005'") == 2
