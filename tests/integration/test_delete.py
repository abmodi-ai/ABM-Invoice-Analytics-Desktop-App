"""Deleting invoices: one invoice (with everything that refers to it) and "start fresh"."""

from __future__ import annotations

from fastapi.testclient import TestClient

from invoice_analytics.context import Engine
from invoice_analytics.data_admin import delete_invoices

from .test_api import _admin, _user, client  # noqa: F401 - pytest fixture
from .test_trial_invoices import MILESTONES, PASSTHRU, REPEAT, _flags, _ingest, ruled_site_invoice


def _load(engine: Engine) -> tuple[int, int]:
    _ingest(engine, "a.pdf", ruled_site_invoice("EX_006", "04/07/2023", MILESTONES, PASSTHRU))
    _ingest(engine, "b.pdf", ruled_site_invoice("EX_015", "10/02/2023", REPEAT, []))
    a = engine.db.scalar("SELECT id FROM invoices WHERE invoice_number_raw='EX_006'")
    b = engine.db.scalar("SELECT id FROM invoices WHERE invoice_number_raw='EX_015'")
    return int(a), int(b)


def _count(engine: Engine, table: str) -> int:
    return int(engine.db.scalar(f"SELECT COUNT(*) FROM {table}"))


def test_delete_one_invoice_removes_it_and_every_flag_involving_it(engine: Engine) -> None:
    a, b = _load(engine)
    engine.store.load_all()
    assert "CLN-011" in _flags(engine)  # repeat imaging on b against the scans on a
    out = delete_invoices(engine, [b], user_id=None)
    assert out["invoices"] == 1 and out["documents"] == 1
    assert engine.db.scalar("SELECT COUNT(*) FROM invoices WHERE id=?", (b,)) == 0
    assert engine.db.scalar("SELECT COUNT(*) FROM invoice_lines WHERE invoice_id=?", (b,)) == 0
    left = _flags(engine)
    assert "CLN-011" not in left  # its counterpart invoice is gone
    assert "CLN-010" in left and "CLN-012" in left  # flags within invoice a stay
    assert _count(engine, "documents") == 1 and _count(engine, "document_blobs") == 1
    # the detection store no longer sees the deleted invoice
    with engine.store.locked() as con:
        assert con.execute("SELECT COUNT(*) FROM inv WHERE id=?", [b]).fetchone()[0] == 0
    ev = engine.db.one("SELECT action, entity_id FROM audit_log ORDER BY id DESC LIMIT 1")
    assert ev["action"] == "INVOICE_DELETE" and ev["entity_id"] == str(b)


def test_start_fresh_deletes_all_invoice_data_and_reimport_works(engine: Engine) -> None:
    _load(engine)
    out = delete_invoices(engine, None, user_id=None)
    assert out["invoices"] == 2
    for t in ("invoices", "invoice_lines", "documents", "document_blobs", "flags", "patients"):
        assert _count(engine, t) == 0, t
    assert engine.db.one("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1")["action"] == "DATA_DELETE_ALL"
    # the same files can be brought in again and are detected afresh
    _load(engine)
    assert {"CLN-010", "CLN-011", "CLN-012"} <= set(_flags(engine))


def test_delete_api_is_admin_only(client: TestClient, engine: Engine) -> None:  # noqa: F811
    a, _ = _load(engine)
    admin = _admin(client)
    reviewer = _user(client, admin, "rev", "REVIEWER")
    assert client.delete(f"/invoices/{a}", headers=reviewer).status_code == 403
    assert client.delete("/invoices/999999", headers=admin).status_code == 404
    assert client.delete(f"/invoices/{a}", headers=admin).json()["invoices"] == 1
    b = _ingest(engine, "c.pdf", ruled_site_invoice("EX_020", "10/02/2023", REPEAT, []))["persist"]["invoice_ids"][0]
    assert client.post("/invoices/delete", headers=reviewer, json={"invoice_ids": [b]}).status_code == 403
    assert client.post("/invoices/delete", headers=admin, json={"invoice_ids": [b, 999999]}).status_code == 404
    assert _count(engine, "invoices") == 2  # nothing deleted when one id is unknown
    assert client.post("/invoices/delete", headers=admin, json={"invoice_ids": [b]}).json()["invoices"] == 1
    assert client.post("/data/delete-all", headers=admin, json={"confirm": "yes"}).status_code == 400
    assert client.post("/data/delete-all", headers=admin, json={"confirm": "DELETE"}).json()["invoices"] == 1
    assert _count(engine, "invoices") == 0
