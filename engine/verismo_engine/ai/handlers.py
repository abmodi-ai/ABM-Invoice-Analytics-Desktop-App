"""Job handlers. AI handlers only produce ai_suggestions; system handlers run engine operations."""

from __future__ import annotations

from typing import Any

from verismo_engine.ai.jobs import JobContext, handler
from verismo_engine.context import Engine


@handler("AI_EXPLAIN")
def _explain(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from verismo_engine.ai.tasks.explain import explain_flag

    return explain_flag(eng, int(p["flag_id"]))


@handler("AI_TRIAGE")
def _triage(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from verismo_engine.ai.agent.loop import triage_flag

    return triage_flag(eng, int(p["flag_id"]), cancelled=ctx.cancelled)


@handler("AI_ASK")
def _ask(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from verismo_engine.ai.tasks.nlq import ask

    return ask(eng, str(p["question"]), p.get("user_id"))


@handler("AI_EXTRACT")
def _extract(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from verismo_engine.ai.tasks.extract import extract_draft

    return extract_draft(eng, int(p["draft_id"]))


@handler("DETECT_SWEEP")
def _sweep(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from verismo_engine.scoring.pipeline import detect

    if p.get("reload"):
        eng.store.load_all()
    return detect(eng, user_id=p.get("user_id")).stats()


@handler("LINKAGE")
def _linkage(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from verismo_engine.linkage.parties import suggest_merges
    from verismo_engine.linkage.patients import resolve_patients

    out = {
        "patients": resolve_patients(eng, user_id=p.get("user_id")),
        "parties": suggest_merges(eng, p.get("user_id")),
    }
    if p.get("then_sweep", True):
        from verismo_engine.scoring.pipeline import detect

        out["sweep"] = detect(eng, user_id=p.get("user_id")).stats()
    return out


@handler("INGEST_FILE")
def _ingest(eng: Engine, p: dict[str, Any], ctx: JobContext) -> Any:
    from pathlib import Path

    from verismo_engine.ingest.service import NeedsMapping, ingest_bytes

    path = Path(p["path"])
    try:
        return ingest_bytes(
            eng,
            path.name,
            path.read_bytes(),
            user_id=p.get("user_id"),
            template_id=p.get("template_id"),
            mapping=p.get("mapping"),
            options=p.get("options") or {},
        ).as_dict()
    except NeedsMapping as e:
        return {"needs_mapping": True, "headers": e.headers, "suggestion": e.suggestion, "path": str(path)}
