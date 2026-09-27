"""AI layer: explanation post-check, agent citation validation, NL->SQL safety, write guard, AI Off."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from invoice_analytics.ai.guard import ai_connection
from invoice_analytics.ai.tasks.nlq import UnsafeSQL, run_readonly, validate_sql
from invoice_analytics.context import Engine

from ..fake_llm import FakeLLM
from .test_ingest_detect import ingest, row


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLLM]:
    f = FakeLLM().start()
    monkeypatch.setenv("IA_LLM_BASE_URL", f.url)
    yield f
    f.stop()


@pytest.fixture
def flagged(engine: Engine) -> Engine:
    ingest(
        engine,
        "a.csv",
        row("Acme", "INV-0045", "2026-03-10", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"),
    )
    ingest(
        engine,
        "b.csv",
        row("Acme", "INV-0054", "2026-04-02", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"),
    )
    return engine


def _cln001(eng: Engine) -> int:
    return int(eng.db.scalar("SELECT id FROM flags WHERE rule_id='CLN-001'"))


def test_ai_off_path_works_and_enqueues_nothing(flagged: Engine) -> None:
    assert flagged.settings.get("ai.tier") == "OFF"
    assert flagged.db.scalar("SELECT COUNT(*) FROM flags WHERE rule_id='CLN-001'") == 1
    assert flagged.db.scalar("SELECT COUNT(*) FROM jobs") == 0


def test_explain_passes_post_check(flagged: Engine, llm: FakeLLM) -> None:
    from invoice_analytics.ai.tasks.explain import explain_flag

    flagged.settings.set("ai.tier", "LITE")
    out = explain_flag(flagged, _cln001(flagged))
    assert out["status"] == "OK", out
    s = flagged.db.one("SELECT * FROM ai_suggestions WHERE id=?", (out["suggestion_id"],))
    assert s["task"] == "EXPLAIN" and "97110" in s["output"]


def test_hallucinated_explanation_is_discarded(flagged: Engine, llm: FakeLLM) -> None:
    from invoice_analytics.ai.tasks.explain import explain_flag

    flagged.settings.set("ai.tier", "LITE")
    llm.mode = "hallucinate"
    out = explain_flag(flagged, _cln001(flagged))
    assert out["status"] == "INVALID"
    assert any("99999" in p for p in out["problems"]) and any("4,321" in p for p in out["problems"])


def test_agent_verdict_with_valid_citations(flagged: Engine, llm: FakeLLM) -> None:
    from invoice_analytics.ai.agent.loop import triage_flag

    flagged.settings.set("ai.tier", "LITE")
    out = triage_flag(flagged, _cln001(flagged))
    assert out["status"] == "OK" and out["verdict"] == "DUPLICATE" and out["tool_calls"] == 2
    trace = json.loads(flagged.db.scalar("SELECT trace FROM ai_suggestions WHERE id=?", (out["suggestion_id"],)))
    assert [t["name"] for t in trace if t["type"] == "tool"] == ["get_flag", "diff_invoices"]


def test_agent_uncited_verdict_becomes_uncertain(flagged: Engine, llm: FakeLLM) -> None:
    from invoice_analytics.ai.agent.loop import triage_flag

    flagged.settings.set("ai.tier", "LITE")
    llm.mode = "bad_citation"
    out = triage_flag(flagged, _cln001(flagged))
    assert out["status"] == "INVALID" and out["verdict"] == "UNCERTAIN"


def test_jobs_run_through_queue_and_flag_open_prioritises_explain(flagged: Engine, llm: FakeLLM) -> None:
    flagged.settings.set("ai.tier", "LITE")
    ingest(
        flagged,
        "c.csv",
        row("Acme", "INV-0099", "2026-05-02", "125.00", 1, "Maria", "Garcia", "2026-03-04", "97110", charge="125.00"),
    )
    kinds = {r["kind"] for r in flagged.db.query("SELECT kind FROM jobs")}
    assert {"AI_EXPLAIN", "AI_TRIAGE"} <= kinds
    ran = flagged.jobs.run_pending()
    assert ran >= 2
    assert flagged.db.scalar("SELECT COUNT(*) FROM jobs WHERE status='DONE'") == ran
    assert flagged.db.scalar("SELECT COUNT(*) FROM ai_suggestions WHERE status='OK'") >= 2


def test_ai_connection_cannot_write_flags_or_reviews(flagged: Engine) -> None:
    conn = ai_connection(flagged)
    try:
        for sql in (
            "UPDATE flags SET status='DISMISSED'",
            "DELETE FROM flags",
            "INSERT INTO reviews(flag_id, reviewer_user_id, decision, pair_key, decided_at) VALUES (1,1,'NOT_DUPLICATE','x','t')",
            "UPDATE invoices SET total_cents=0",
            "DROP TABLE flags",
            "DELETE FROM audit_log",
        ):
            with pytest.raises(Exception):
                conn.execute(sql)
        conn.execute("INSERT INTO jobs(kind) VALUES ('X')")  # allowed
    finally:
        conn.close()


def test_ai_module_sources_never_write_decision_tables() -> None:
    from pathlib import Path

    import invoice_analytics.ai as ai_pkg

    root = Path(ai_pkg.__file__).parent
    bad = []
    for p in root.rglob("*.py"):
        s = p.read_text().upper()
        for t in ("FLAGS", "REVIEWS", "INVOICES", "INVOICE_LINES", "AUDIT_LOG"):
            if f"UPDATE {t} " in s or f"INSERT INTO {t}(" in s or f"INSERT INTO {t} " in s or f"DELETE FROM {t}" in s:
                bad.append(f"{p.name}: {t}")
    assert not bad, bad


def test_nlq_end_to_end(flagged: Engine, llm: FakeLLM) -> None:
    from invoice_analytics.ai.tasks.nlq import ask

    flagged.settings.set("ai.tier", "LITE")
    out = ask(flagged, "which vendors have the most invoices?")
    assert out["status"] == "OK", out
    assert out["result"]["columns"] == ["party_name", "n"]
    assert "LIMIT 500" in out["result"]["sql"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM v_flags",
        "UPDATE v_flags SET tier='INFO'",
        "DROP TABLE flags",
        "PRAGMA key='x'",
        "ATTACH DATABASE 'x.db' AS x",
        "SELECT * FROM flags",
        "SELECT * FROM patients",
        "SELECT * FROM users",
        "SELECT 1; DELETE FROM flags",
        "SELECT load_extension('evil')",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM main.v_flags",
        "WITH x AS (SELECT * FROM audit_log) SELECT * FROM x",
        "SELECT * FROM v_flags WHERE flag_id IN (SELECT id FROM reviews)",
        "VACUUM",
        "CREATE TABLE t(a)",
        "SELECT readfile('/etc/passwd')",
        "INSERT INTO v_flags VALUES (1)",
        "SELECT * FROM pragma_table_info('users')",
    ],
)
def test_nlq_rejects_unsafe(sql: str) -> None:
    with pytest.raises(UnsafeSQL):
        validate_sql(sql)


def test_nlq_limit_enforced_and_sensitive_columns_blocked(flagged: Engine) -> None:
    assert "LIMIT 500" in validate_sql("SELECT * FROM v_flags LIMIT 100000")
    assert "LIMIT 7" in validate_sql("SELECT * FROM v_flags LIMIT 7")
    res = run_readonly(flagged, "SELECT rule_id, tier FROM v_flags")
    assert res["row_count"] >= 1


def test_setup_state_reports_missing_runner_and_models(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from invoice_analytics.ai import service

    monkeypatch.delenv("IA_LLAMA_SERVER", raising=False)
    monkeypatch.delenv("IA_LLM_BASE_URL", raising=False)
    monkeypatch.setattr("shutil.which", lambda _n: None)
    st = service.setup_state(engine)
    assert st == {"runner_found": False, "models_installed": []}
    d = engine.config.models_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.gguf").write_bytes(b"x")
    (d / "manifest.json").write_text(json.dumps({"models": [{"file": "m.gguf"}, {"file": "missing.gguf"}]}))
    monkeypatch.setenv("IA_LLAMA_SERVER", "/opt/llama-server")
    assert service.setup_state(engine) == {"runner_found": True, "models_installed": ["m.gguf"]}
