"""Smoke-test a frozen (PyInstaller) engine build end to end.

    uv run python tools/frozen_smoke.py build/engine/invoice-analytics-engine/invoice-analytics-engine[.exe]

Starts the binary on a free loopback port with a fresh data dir, then: health → setup admin →
login → upload a digital PDF, a scanned PDF (OCR via the spawned extraction subprocess) and a CSV →
wait for the ingest jobs → full sweep → audit integrity. Exits non-zero on any failure.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


def main() -> int:
    exe = Path(sys.argv[1])
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    token = secrets.token_hex(32)
    data = Path(tempfile.mkdtemp(prefix="ia-frozen-"))
    env = {
        **os.environ,
        "IA_DATA_DIR": str(data),
        "IA_PORT": str(port),
        "IA_TOKEN": token,
        "IA_KEYSTORE": os.environ.get("IA_KEYSTORE", "dpapi" if os.name == "nt" else "file"),
    }
    proc = subprocess.Popen([str(exe)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    c = httpx.Client(
        base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=60, trust_env=False
    )
    try:
        for _ in range(120):
            try:
                if c.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise SystemExit("engine did not become healthy")
        pw = secrets.token_urlsafe(12)
        c.post(
            "/setup/admin",
            json={"username": "smoke", "display_name": "Smoke", "password": pw, "create_recovery_key": False},
        ).raise_for_status()
        sess = c.post("/auth/login", json={"username": "smoke", "password": pw}).json()["session_token"]
        h = {"X-IA-Session": sess}
        from integration.test_documents import LINES_OK, make_pdf
        from integration.test_ingest_detect import HEADER, MAPPING, row

        digital = make_pdf("HMS-20931", "03/14/2026", LINES_OK, "475.75")
        scanned = make_pdf("HMS-77120", "03/14/2026", LINES_OK, "475.75", scanned=True)
        csv = (HEADER + row("Acme", "1", "2026-03-10", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213")).encode()
        import json

        r = c.post(
            "/ingest/files",
            headers=h,
            data={"mapping": json.dumps(MAPPING)},
            files=[
                ("files", ("digital.pdf", digital, "application/pdf")),
                ("files", ("scanned.pdf", scanned, "application/pdf")),
                ("files", ("claims.csv", csv, "text/csv")),
            ],
        )
        r.raise_for_status()
        jobs = {j["id"]: j for j in r.json()["jobs"]}
        deadline = time.time() + 180
        while time.time() < deadline:
            states = {jid: c.get(f"/ingest/jobs/{jid}", headers=h).json() for jid in jobs}
            if all(s["status"] not in ("QUEUED", "RUNNING") for s in states.values()):
                break
            time.sleep(1)
        for j in states.values():
            method = (j.get("result") or {}).get("method")
            print(f"{j['filename']}: {j['status']} method={method} {j.get('error') or ''}")
        by_name = {j["filename"]: j for j in states.values()}
        assert by_name["digital.pdf"]["status"] == "DONE", by_name["digital.pdf"]
        assert by_name["digital.pdf"]["result"]["method"] == "PDF_TEXT"
        assert by_name["scanned.pdf"]["status"] in ("DONE", "NEEDS_REVIEW"), by_name["scanned.pdf"]
        assert by_name["scanned.pdf"]["result"]["method"] == "OCR", by_name["scanned.pdf"]
        assert by_name["claims.csv"]["status"] == "DONE", by_name["claims.csv"]
        jid = c.post("/detect/sweep", headers=h).json()["job_id"]
        for _ in range(60):
            if c.get(f"/detect/sweep/{jid}", headers=h).json()["status"] == "DONE":
                break
            time.sleep(1)
        else:
            raise SystemExit("sweep did not finish")
        assert c.post("/audit/verify", headers=h).json()["ok"]
        print("frozen engine smoke test: OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
