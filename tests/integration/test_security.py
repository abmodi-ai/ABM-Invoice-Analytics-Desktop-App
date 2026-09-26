"""Security tests (spec 9 and 11.1): no network, no PHI in logs, malformed-file fuzzing,
NL->SQL adversarial fuzzing, path handling."""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from invoice_analytics.context import Engine
from invoice_analytics.ingest.service import NeedsMapping, ingest_bytes

from ..fake_llm import FakeLLM
from .test_ingest_detect import HEADER, MAPPING, row

REPO = Path(__file__).resolve().parents[2]
UNIQUE_FIRST, UNIQUE_LAST, UNIQUE_DOB, UNIQUE_MEMBER = "Zyxwva", "Qqplornik", "1937-08-19", "MZ99887766"


def test_no_phi_in_logs(engine: Engine, tmp_path: Path) -> None:
    from invoice_analytics.logging_setup import setup_logging

    h = setup_logging(tmp_path / "logs", logging.DEBUG)
    try:
        body = HEADER.replace("Member ID", "Member ID") + (
            f"Acme,12-3456789,INV-1,2026-03-10,100.00,1,{UNIQUE_FIRST},{UNIQUE_LAST},{UNIQUE_DOB},{UNIQUE_MEMBER},"
            f"2026-03-04,97110,,1,100.00,1234567893,exercise,,\n"
        )
        for i in range(2):
            ingest_bytes(engine, f"f{i}.csv", body.replace("INV-1", f"INV-{i}").encode(), mapping=MAPPING)
        # deliberately hostile log lines: a careless developer logging PHI still gets scrubbed
        logging.getLogger("invoice_analytics.test").warning(
            "patient name=%s dob %s member %s", UNIQUE_LAST, UNIQUE_DOB, UNIQUE_MEMBER
        )
        try:
            raise ValueError(f"bad row for {UNIQUE_DOB} {UNIQUE_MEMBER}")
        except ValueError:
            logging.getLogger("invoice_analytics.test").exception("failure")
        h.flush()
    finally:
        logging.getLogger().removeHandler(h)
    text = (tmp_path / "logs" / "engine.log").read_text()
    assert text  # something was logged
    for secret in (UNIQUE_LAST.upper(), UNIQUE_LAST, UNIQUE_FIRST, UNIQUE_DOB, UNIQUE_MEMBER):
        assert secret not in text, f"{secret} leaked into logs"


@pytest.mark.slow
def test_engine_process_opens_no_non_loopback_sockets(tmp_path: Path) -> None:
    """Runs the real engine process through setup, ingest and a sweep, sampling its sockets."""
    token = "s" * 40
    env = {
        **os.environ,
        "IA_DATA_DIR": str(tmp_path / "d"),
        "IA_KEYSTORE": "file",
        "IA_TOKEN": token,
        "IA_PORT": "0",
        "IA_EMBEDDER": "hashing",
    }
    p = subprocess.Popen([sys.executable, "-m", "invoice_analytics"], env=env, stdout=subprocess.PIPE, text=True)
    try:
        line = p.stdout.readline()  # type: ignore[union-attr]
        port = json.loads(line)["port"]
        base = f"http://127.0.0.1:{port}"
        c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, trust_env=False, timeout=30)
        for _ in range(100):
            try:
                if c.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        proc = psutil.Process(p.pid)
        seen: list[tuple[str, str]] = []

        def sample() -> None:
            # On Windows the venv python.exe is a launcher that runs the real interpreter as a
            # child process, so sample the whole process tree.
            for pr in [proc, *proc.children(recursive=True)]:
                try:
                    conns = pr.net_connections(kind="inet")
                except psutil.Error:
                    continue
                for conn in conns:
                    la = conn.laddr.ip if conn.laddr else ""
                    ra = conn.raddr.ip if conn.raddr else ""
                    seen.append((la, ra))

        c.post("/setup/admin", json={"username": "admin", "display_name": "A", "password": "correct horse battery"})
        tok = c.post("/auth/login", json={"username": "admin", "password": "correct horse battery"}).json()[
            "session_token"
        ]
        h = {"X-IA-Session": tok}
        body = HEADER + row("Acme", "1", "2026-03-10", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213")
        c.post(
            "/ingest/csv/mapping-templates",
            headers=h,
            json={"name": "t", "headers": HEADER.strip().split(","), "mapping": MAPPING},
        )
        for i in range(3):
            c.post(
                "/ingest/files",
                headers=h,
                files=[("files", (f"{i}.csv", body.replace(",1,2026-03-10", f",{i},2026-03-10").encode(), "text/csv"))],
            )
            sample()
        c.post("/detect/sweep", headers=h)
        for _ in range(20):
            sample()
            time.sleep(0.1)
        bad = [s for s in seen if s[0] not in ("127.0.0.1", "::1", "") or s[1] not in ("127.0.0.1", "::1", "")]
        assert seen, "no sockets observed"
        assert not bad, f"non-loopback sockets: {bad}"
    finally:
        p.terminate()
        p.wait(10)


def test_llm_client_refuses_non_loopback() -> None:
    from invoice_analytics.ai.runtime import AIUnavailable, LLMClient

    for url in ("http://10.0.0.5:8080", "https://api.example.com", "http://169.254.169.254"):
        with pytest.raises(AIUnavailable):
            LLMClient(url, "k", "m", None)


def test_source_has_no_outbound_http() -> None:
    """Only the loopback LLM client and the build-time refdata fetcher may use HTTP clients."""
    offenders = []
    for p in (REPO / "engine").rglob("*.py"):
        s = p.read_text()
        if ("httpx." in s or "requests." in s or "urllib.request" in s) and p.name not in ("runtime.py",):
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, offenders


# ---------------------------------------------------------------- malformed-file fuzzing
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.binary(max_size=4000))
def test_fuzz_x12_never_crashes(engine: Engine, data: bytes) -> None:
    out = ingest_bytes(engine, "f.837", b"ISA*" + data, detect=False)
    assert out.parse.method == "X12"


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text(max_size=3000))
def test_fuzz_csv_never_crashes(engine: Engine, text: str) -> None:
    try:
        ingest_bytes(engine, "f.csv", (HEADER + text).encode(), mapping=MAPPING, detect=False)
    except NeedsMapping:
        pass


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.binary(min_size=0, max_size=3000))
def test_fuzz_pdf_never_crashes(engine: Engine, data: bytes) -> None:
    out = ingest_bytes(engine, "f.pdf", b"%PDF-1.4\n" + data, detect=False, options={"_in_process": True})
    assert out.parse is not None


def test_x12_truncated_and_hostile_segments(engine: Engine) -> None:
    for payload in [
        b"ISA*00*",
        b"ISA" + b"*" * 200,
        b"ISA*00*          *00*          *ZZ*A*ZZ*B*260101*1200*^*00501*1*0*T*:~ST*837*1~CLM*~SV1*~",
        b"ISA" + b"~" * 1000,
    ]:
        out = ingest_bytes(engine, "h.x12", payload, detect=False)
        assert out.persist is None or out.persist.invoice_ids == []


def test_upload_filename_cannot_traverse(engine: Engine) -> None:
    body = HEADER + row("Acme", "1", "2026-03-10", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213")
    out = ingest_bytes(engine, "../../../etc/evil.csv", body.encode(), mapping=MAPPING, detect=False)
    assert engine.db.scalar("SELECT source_path FROM documents WHERE id=?", (out.persist.document_id,)) == "evil.csv"


# ---------------------------------------------------------------- NL->SQL adversarial fuzz (500 prompts)
DANGEROUS = [
    "DELETE FROM flags",
    "DROP TABLE flags",
    "UPDATE flags SET status='DISMISSED'",
    "INSERT INTO reviews VALUES(1)",
    "PRAGMA key='x'",
    "PRAGMA writable_schema=1",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "DETACH DATABASE main",
    "VACUUM INTO '/tmp/leak.db'",
    "CREATE TABLE t(a)",
    "ALTER TABLE flags ADD c",
    "SELECT * FROM patients",
    "SELECT first_name_enc FROM patients",
    "SELECT password_hash FROM users",
    "SELECT * FROM audit_log",
    "SELECT * FROM sqlite_master",
    "SELECT load_extension('x')",
    "SELECT readfile('/etc/passwd')",
    "SELECT writefile('/tmp/x','y')",
    "SELECT * FROM pragma_table_info('users')",
    "REINDEX",
    "ANALYZE",
    "BEGIN; DELETE FROM flags; COMMIT",
    "SELECT * FROM v_flags; DROP TABLE flags",
    "SELECT * FROM main.users",
    "WITH x AS (SELECT * FROM users) SELECT * FROM x",
    "SELECT * FROM v_flags WHERE 1=(SELECT 1 FROM users)",
    "SELECT * FROM v_flags UNION SELECT username,password_hash,1,1,1,1,1,1,1,1,1 FROM users",
    "SELECT * FROM (SELECT * FROM patients)",
    "SELECT * FROM v_invoices JOIN patients ON 1=1",
    "SELECT * FROM temp.sqlite_master",
    "SELECT sqlite_version()",
    "SELECT randomblob(1000000000)",
    "SELECT * FROM json_each((SELECT group_concat(password_hash) FROM users))",
]


def adversarial_prompts(n: int = 500) -> list[str]:
    rng = random.Random(1234)
    wraps = [
        lambda s: s,
        lambda s: s.lower(),
        lambda s: f"/* ok */ {s}",
        lambda s: f"{s} -- harmless",
        lambda s: f"SELECT 1; {s}",
        lambda s: f"{s};",
        lambda s: s.replace(" ", "\n"),
        lambda s: f"  {s}  ",
        lambda s: s.replace("FROM", "FrOm"),
        lambda s: f"EXPLAIN {s}",
        lambda s: f"{s} LIMIT 1",
        lambda s: f"SELECT * FROM v_flags WHERE tier IN ({s})",
    ]
    out = []
    while len(out) < n:
        out.append(rng.choice(wraps)(rng.choice(DANGEROUS)))
    return out


def test_nlq_adversarial_fuzz_500(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from invoice_analytics.ai.tasks.nlq import VIEWS, ask

    ingest_bytes(
        engine,
        "a.csv",
        (HEADER + row("Acme", "1", "2026-03-10", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213")).encode(),
        mapping=MAPPING,
    )
    llm = FakeLLM().start()
    monkeypatch.setenv("IA_LLM_BASE_URL", llm.url)
    engine.settings.set("ai.tier", "LITE")

    def snapshot() -> tuple:
        return (
            engine.db.scalar("SELECT COUNT(*) FROM flags"),
            engine.db.scalar("SELECT COUNT(*) FROM users"),
            engine.db.scalar("SELECT COUNT(*) FROM reviews"),
            engine.db.scalar("SELECT group_concat(name) FROM sqlite_master"),
        )

    before = snapshot()
    executed_bad = 0
    try:
        for q in adversarial_prompts(500):
            llm.sql_override = q
            out = ask(engine, "adversarial")
            if out["status"] == "OK":
                import sqlglot
                from sqlglot import exp

                tree = sqlglot.parse_one(out["result"]["sql"], read="sqlite")
                tables = {t.name.lower() for t in tree.find_all(exp.Table)}
                ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
                if not isinstance(tree, (exp.Select, exp.Union)) or tables - set(VIEWS) - ctes:
                    executed_bad += 1
                for col in out["result"]["columns"]:
                    assert col.lower() not in ("password_hash", "first_name_enc", "last_name_enc", "patient_key")
    finally:
        llm.stop()
    assert executed_bad == 0
    after = snapshot()
    assert before[:3] == after[:3] and before[3] == after[3]
