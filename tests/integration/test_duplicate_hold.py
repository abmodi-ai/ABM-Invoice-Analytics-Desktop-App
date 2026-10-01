"""Uploads that repeat something already billed are held back until the user confirms them, and every
line that may be a duplicate is marked on the invoice."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from invoice_analytics.context import Engine
from invoice_analytics.ingest.service import ingest_bytes

from .test_api import _admin, _wait_ingest, client  # noqa: F401 - pytest fixture
from .test_trial_invoices import FIRST_VISITS, _ingest, stacked_site_invoice

REBILL = [
    ("XYZ-201-1003", "Month 1 Day 1", "08/12/2023", "1500.00"),  # already billed on 500123 (dated 08/05)
    ("XYZ-201-1003", "Month 2 Day 1", "09/02/2023", "2600.00"),
]
NEW_ONLY = [("XYZ-201-1003", "Month 2 Day 7", "09/08/2023", "2385.00")]
HOLD = {"_in_process": True, "hold_duplicates": True}


def _counts(eng: Engine) -> dict[str, int]:
    return {
        t: int(eng.db.scalar(f"SELECT COUNT(*) FROM {t}"))
        for t in ("invoices", "invoice_lines", "documents", "flags", "parties", "patients")
    }


def test_held_upload_saves_nothing_and_says_what_matched(engine: Engine) -> None:
    _ingest(engine, "first.pdf", stacked_site_invoice("500123", FIRST_VISITS))
    before = _counts(engine)
    out = ingest_bytes(engine, "second.pdf", stacked_site_invoice("500200", REBILL), options=HOLD)
    assert out.persist is None and out.held is not None
    assert [i["number"] for i in out.held["invoices"]] == ["500200"]
    assert [i["number"] for i in out.held["earlier_invoices"]] == ["500123"]
    assert out.held["lines_checked"] == 2 and out.held["lines_matched"] == 1
    assert "CLN-013" in {r["rule_id"] for r in out.held["repeats"]}
    assert _counts(engine) == before  # no invoice, line, document, flag, vendor or subject left behind
    assert engine.db.one("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1")["action"] == "INGEST_HELD_DUPLICATE"
    # confirmed: saved and flagged exactly as an unheld upload would be
    ok = ingest_bytes(
        engine, "second.pdf", stacked_site_invoice("500200", REBILL), options={**HOLD, "confirm_duplicates": True}
    )
    assert ok.persist is not None and ok.held is None
    assert engine.db.scalar("SELECT COUNT(*) FROM flags WHERE rule_id='CLN-013' AND active=1") == 1


def test_upload_with_nothing_billed_before_is_saved_straight_away(engine: Engine) -> None:
    _ingest(engine, "first.pdf", stacked_site_invoice("500123", FIRST_VISITS))
    out = ingest_bytes(engine, "next.pdf", stacked_site_invoice("500300", NEW_ONLY), options=HOLD)
    assert out.held is None and out.persist is not None


def test_ingest_screen_flow_discard_then_upload_anyway(client: TestClient, engine: Engine) -> None:  # noqa: F811
    h = _admin(client)
    _ingest(engine, "first.pdf", stacked_site_invoice("500123", FIRST_VISITS))
    pdf = stacked_site_invoice("500200", REBILL)
    opts = {"options": json.dumps({"_in_process": True})}

    def upload() -> dict:
        r = client.post(
            "/ingest/files", headers=h, files=[("files", ("second.pdf", pdf, "application/pdf"))], data=opts
        )
        return _wait_ingest(client, h, r.json()["jobs"][0]["id"])

    job = upload()
    assert job["status"] == "NEEDS_CONFIRMATION" and job["result"]["held"]["earlier_invoices"][0]["number"] == "500123"
    assert client.post(f"/ingest/jobs/{job['id']}/discard", headers=h).json()["status"] == "DISCARDED"
    assert client.post(f"/ingest/jobs/{job['id']}/confirm", headers=h).status_code == 404  # bytes are gone
    assert engine.db.scalar("SELECT COUNT(*) FROM invoices") == 1

    job = upload()
    assert job["status"] == "NEEDS_CONFIRMATION"
    client.post(f"/ingest/jobs/{job['id']}/confirm", headers=h)
    done = _wait_ingest(client, h, job["id"])
    assert done["status"] == "DONE" and done["result"]["held"] is None
    assert engine.db.scalar("SELECT COUNT(*) FROM invoices") == 2
    actions = [r["action"] for r in engine.db.query("SELECT action FROM audit_log ORDER BY id")]
    assert "INGEST_DUPLICATE_DISCARDED" in actions and "INGEST_DUPLICATE_CONFIRMED" in actions


def test_possible_duplicate_lines_are_marked_on_both_invoices(client: TestClient, engine: Engine) -> None:  # noqa: F811
    h = _admin(client)
    first = _ingest(engine, "first.pdf", stacked_site_invoice("500123", FIRST_VISITS))["persist"]["invoice_ids"][0]
    second = _ingest(engine, "second.pdf", stacked_site_invoice("500200", REBILL))["persist"]["invoice_ids"][0]
    new = client.get(f"/invoices/{second}", headers=h).json()["lines"]
    marked = {li["visit_label"]: li["duplicates"] for li in new}
    assert marked["M2D1"] == []  # a visit not billed before
    dup = marked["M1D1"][0]
    assert (
        dup["other_invoice_number"] == "500123" and not dup["same_invoice"] and dup["rule_id"] in ("CLN-013", "CLN-011")
    )
    old = client.get(f"/invoices/{first}", headers=h).json()["lines"]
    back = {li["visit_label"]: li["duplicates"] for li in old}
    assert back["M1D1"] and back["M1D1"][0]["other_invoice_number"] == "500200"
    assert back["D-5"] == [] and back["M1D2"] == []
    # dismissed pairs stop being marked
    for f in engine.db.query("SELECT id FROM flags WHERE active=1"):
        assert (
            client.post(
                f"/flags/{f['id']}/review",
                headers=h,
                json={"decision": "NOT_DUPLICATE", "reason_code": "LEGITIMATE_REPEAT"},
            ).status_code
            == 200
        )
    assert all(not li["duplicates"] for li in client.get(f"/invoices/{second}", headers=h).json()["lines"])
