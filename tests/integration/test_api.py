"""HTTP API: auth, roles, launch-token/origin/host checks, and the ingest -> review -> report flow."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from verismo_engine.api.app import create_app
from verismo_engine.context import Engine

from .test_ingest_detect import HEADER, MAPPING, row

TOKEN = "t" * 40


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    engine.config.token = TOKEN
    app = create_app(engine)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield c


def _admin(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/setup/admin", json={"username": "admin", "display_name": "Admin", "password": "correct horse battery"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["recovery_key"].count("-") == 7
    tok = client.post("/auth/login", json={"username": "admin", "password": "correct horse battery"}).json()[
        "session_token"
    ]
    return {"X-Verismo-Session": tok}


def _user(client: TestClient, admin: dict[str, str], name: str, role: str) -> dict[str, str]:
    assert (
        client.post(
            "/users",
            headers=admin,
            json={"username": name, "display_name": name, "role": role, "password": f"{name}-password-123"},
        ).status_code
        == 200
    )
    tok = client.post("/auth/login", json={"username": name, "password": f"{name}-password-123"}).json()[
        "session_token"
    ]
    return {"X-Verismo-Session": tok}


def _wait_ingest(client: TestClient, h: dict[str, str], job_id: int) -> dict:
    for _ in range(200):
        j = client.get(f"/ingest/jobs/{job_id}", headers=h).json()
        if j["status"] not in ("QUEUED", "RUNNING"):
            return j
        time.sleep(0.05)
    raise AssertionError("ingest job did not finish")


def test_launch_token_origin_and_host_checks(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/setup/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/setup/status", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/setup/status", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/setup/status").status_code == 200


def test_setup_once_login_and_roles(client: TestClient) -> None:
    admin = _admin(client)
    assert (
        client.post(
            "/setup/admin", json={"username": "xx", "display_name": "x", "password": "another password"}
        ).status_code
        == 409
    )
    assert client.get("/auth/me", headers=admin).json()["role"] == "ADMIN"
    viewer = _user(client, admin, "val", "VIEWER")
    assert client.get("/flags", headers=viewer).status_code == 200
    assert client.post("/detect/sweep", headers=viewer).status_code == 403
    assert client.get("/audit", headers=viewer).status_code == 403
    assert client.get("/flags").status_code == 401  # no session
    assert client.post("/auth/login", json={"username": "admin", "password": "nope nope nope"}).status_code == 401


def test_ingest_mapping_wizard_review_and_reports(client: TestClient, engine: Engine) -> None:
    admin = _admin(client)
    rev = _user(client, admin, "rita", "REVIEWER")
    body1 = HEADER + row(
        "Acme Therapy", "INV-0045", "2026-03-10", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"
    )
    body2 = HEADER + row(
        "Acme Therapy", "INV-0045", "2026-04-02", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"
    )
    r = client.post("/ingest/files", headers=rev, files=[("files", ("a.csv", body1.encode(), "text/csv"))])
    job = _wait_ingest(client, rev, r.json()["jobs"][0]["id"])
    assert (
        job["status"] == "NEEDS_MAPPING" and job["result"]["suggestion"]["invoice_number"]["column"] == "Invoice Number"
    )
    r = client.post(
        f"/ingest/jobs/{job['id']}/mapping",
        headers=rev,
        json={"mapping": MAPPING, "options": {"direction": "AP"}, "save_template_name": "Acme export"},
    )
    assert _wait_ingest(client, rev, job["id"])["status"] == "DONE"
    # the saved template is applied automatically next time
    r = client.post("/ingest/files", headers=rev, files=[("files", ("b.csv", body2.encode(), "text/csv"))])
    j2 = _wait_ingest(client, rev, r.json()["jobs"][0]["id"])
    assert j2["status"] == "DONE" and j2["result"]["detection"]["new_flags"] >= 2
    flags = client.get("/flags", headers=rev).json()
    assert flags["total"] >= 2 and flags["items"][0]["tier"] == "HARD"
    fid = flags["items"][0]["id"]
    d = client.get(f"/flags/{fid}", headers=rev).json()
    assert d["subject_invoice"]["lines"][0]["patient_name"] == "GARCIA, MARIA"
    assert d["evidence"]["matched_fields"]
    r = client.post(
        f"/flags/{fid}/review",
        headers=rev,
        json={"decision": "CONFIRMED_DUPLICATE", "reason_code": "EXACT_RESUBMISSION", "recovered_cents": 12500},
    )
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"
    applied = r.json()["applied_to"]
    assert (
        client.post(
            f"/flags/{fid}/review", headers=rev, json={"decision": "NOT_DUPLICATE", "reason_code": "BOGUS"}
        ).status_code
        == 400
    )
    dash = client.get("/dashboard", headers=rev).json()
    assert dash["amount_recovered_cents"] == 12500
    csv_ = client.get("/reports/rule_precision?format=csv", headers=rev)
    assert csv_.status_code == 200 and "rule_id" in csv_.text
    pdf = client.get("/reports/duplicates?format=pdf", headers=rev)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    exp = client.get("/flags/export.csv?tier=HARD,PROBABLE", headers=rev)
    assert exp.status_code == 200 and "INV-001" in exp.text
    audit = client.get("/audit?action=REVIEW_DECISION", headers=admin).json()
    assert audit["total"] == 1 + len(applied)  # one entry per decided flag, siblings included
    assert client.post("/audit/verify", headers=admin).json()["ok"] is True
    views = client.get("/audit?action=VIEW_PHI", headers=admin).json()
    assert views["total"] >= 1


def test_rules_patch_preview_and_sweep(client: TestClient, engine: Engine) -> None:
    admin = _admin(client)
    rules = client.get("/rules", headers=admin).json()
    assert {r["rule_id"] for r in rules} >= {"INV-001", "CLN-009", "SUP-005"}
    assert client.patch("/rules/INV-005", headers=admin, json={"bogus": 1}).status_code == 400
    r = client.patch("/rules/INV-005", headers=admin, json={"window_days": 30})
    assert r.status_code == 200 and r.json()["config"]["window_days"] == 30
    p = client.post("/rules/preview-impact", headers=admin, json={"changes": {"rules.INV-003": {"enabled": False}}})
    assert p.status_code == 200 and "added" in p.json()
    jid = client.post("/detect/sweep", headers=admin).json()["job_id"]
    engine.jobs.run_pending()
    assert client.get(f"/detect/sweep/{jid}", headers=admin).json()["status"] == "DONE"


def test_refdata_import_requires_trusted_signature(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import verismo_engine.clinical.refdata as rd

    def keypair() -> tuple[str, str]:
        k = Ed25519PrivateKey.generate()
        return (
            k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex(),
            k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex(),
        )

    trusted_priv, trusted_pub = keypair()
    rogue_priv, _ = keypair()
    monkeypatch.setattr(rd, "TRUSTED_PUBLIC_KEYS", [trusted_pub])  # stands in for the embedded release key
    admin = _admin(client)
    good = rd.build_bundle(rd.dev_sample_datasets(), trusted_priv, "TEST")
    r = client.post("/refdata/import", headers=admin, files={"file": ("x.vref", good, "application/zip")})
    assert r.status_code == 200, r.text
    bad = rd.build_bundle(rd.dev_sample_datasets(), rogue_priv, "EVIL")
    r = client.post("/refdata/import", headers=admin, files={"file": ("x.vref", bad, "application/zip")})
    assert r.status_code == 400 and "signature" in r.json()["detail"]
    versions = client.get("/refdata/versions", headers=admin).json()
    assert any(v["dataset"] == "MUE_PRACTITIONER" for v in versions)
    r = client.post("/refdata/frequency-limits", headers=admin, json={"code": "99497", "max_count": 1, "period": "DAY"})
    assert r.status_code == 200


def test_ai_endpoints_off_then_on(client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from ..fake_llm import FakeLLM

    admin = _admin(client)
    assert client.post("/ai/ask", headers=admin, json={"question": "how many flags?"}).status_code == 409
    st = client.get("/ai/status", headers=admin).json()
    assert st["tier"] == "OFF" and "hardware" in st
    llm = FakeLLM().start()
    monkeypatch.setenv("VERISMO_LLM_BASE_URL", llm.url)
    engine.settings.set("ai.tier", "LITE")  # bypass the hardware check for the test machine
    jid = client.post("/ai/ask", headers=admin, json={"question": "top vendors by invoices"}).json()["job_id"]
    engine.jobs.run_pending()
    job = client.get(f"/ai/jobs/{jid}", headers=admin).json()
    assert job["status"] == "DONE", job
    sug = client.get(f"/ai/suggestions/{job['result']['suggestion_id']}", headers=admin).json()
    assert sug["task"] == "NLQ" and sug["output"]["generated"]["sql"].startswith("SELECT")
    r = client.post("/search/sql", headers=admin, json={"sql": "DELETE FROM v_flags"})
    assert r.status_code == 400
    llm.stop()


def test_backup_and_restore_roundtrip(client: TestClient, engine: Engine, tmp_path: Path) -> None:
    from verismo_engine.backup import inspect_backup

    admin = _admin(client)
    engine.settings.set("ai.idle_stop_seconds", 123)
    r = client.post(
        "/backup", headers=admin, json={"dest_dir": str(tmp_path / "bk"), "recovery_key": "AAAAA-BBBBB-CCCCC"}
    )
    assert r.status_code == 200
    path = Path(r.json()["path"])
    meta, _bkey, keyset = inspect_backup(path, recovery_key="AAAAA-BBBBB-CCCCC")
    assert keyset == engine.keys and meta["schema_version"] >= 4
    with pytest.raises(Exception):
        inspect_backup(path, recovery_key="WRONG-KEY")


def test_settings_and_watched_folder(client: TestClient, engine: Engine, tmp_path: Path) -> None:
    admin = _admin(client)
    folder = tmp_path / "inbox"
    folder.mkdir()
    assert client.patch("/settings", headers=admin, json={"ingest.watched_folders": [str(folder)]}).status_code == 200
    assert client.patch("/settings", headers=admin, json={"ai.tier": "LITE"}).status_code == 400
    f = folder / "claims.csv"
    f.write_text(
        HEADER + row("Acme", "77", "2026-03-10", "250.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="250.00")
    )
    import os

    old = time.time() - 10
    os.utime(f, (old, old))
    assert client.post("/ingest/watch/scan", headers=admin).json()["queued"] == 1
    assert client.post("/ingest/watch/scan", headers=admin).json()["queued"] == 0
    jobs = client.get("/ingest/jobs", headers=admin).json()
    assert jobs and jobs[0]["source"].startswith("watch:")


def test_openapi_contract_lists_spec_endpoints(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    required = {
        "/health",
        "/auth/login",
        "/auth/logout",
        "/ingest/files",
        "/ingest/jobs/{job_id}",
        "/ingest/csv/mapping-templates",
        "/invoices",
        "/invoices/{invoice_id}",
        "/flags",
        "/flags/{flag_id}",
        "/flags/{flag_id}/review",
        "/detect/sweep",
        "/detect/sweep/{job_id}",
        "/parties/merge-suggestions",
        "/parties/merge",
        "/parties/unmerge",
        "/patients/link-suggestions",
        "/patients/link",
        "/patients/unlink",
        "/rules",
        "/rules/{rule_id}",
        "/rules/preview-impact",
        "/refdata/versions",
        "/refdata/import",
        "/ai/status",
        "/ai/tier",
        "/ai/explain/{flag_id}",
        "/ai/triage/{flag_id}",
        "/ai/ask",
        "/ai/suggestions/{sid}",
        "/reports/{name}",
        "/audit",
        "/audit/verify",
        "/backup",
        "/restore",
    }
    assert required <= paths, required - paths
    json.dumps(paths, default=list)
