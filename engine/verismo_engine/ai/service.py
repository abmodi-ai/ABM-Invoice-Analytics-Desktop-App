"""Shared helpers for AI tasks: runtime access, prompt loading, tier checks."""

from __future__ import annotations

from importlib import resources
from typing import Any

from verismo_engine.ai.runtime import AIUnavailable, LlamaRuntime, LLMClient
from verismo_engine.context import Engine

DEFAULT_MODELS = {
    "LITE": "qwen3-4b-instruct-q4_k_m.gguf",
    "STANDARD": "qwen3-8b-q4_k_m.gguf",
    "PLUS": "qwen3-30b-a3b-q4_k_m.gguf",
}


def load_prompt(name: str) -> tuple[str, str]:
    """Returns (text, version) for engine/ai/prompts/<name>_vN.md (highest N)."""
    base = resources.files("verismo_engine.ai") / "prompts"
    versions = sorted(
        (p.name for p in base.iterdir() if p.name.startswith(f"{name}_v") and p.name.endswith(".md")),
        key=lambda n: int(n.rsplit("_v", 1)[1].split(".")[0]),
    )
    if not versions:
        raise FileNotFoundError(name)
    fname = versions[-1]
    return (base / fname).read_text(encoding="utf-8"), fname.removesuffix(".md")


def ai_enabled(eng: Engine) -> bool:
    return bool(eng.settings.get("ai.tier", "OFF") != "OFF")


def runtime(eng: Engine) -> LlamaRuntime:
    rt = eng.extras.get("llm_runtime")
    if rt is None:
        rt = LlamaRuntime(eng.config.models_dir, idle_seconds=int(eng.settings.get("ai.idle_stop_seconds", 600)))
        eng.extras["llm_runtime"] = rt
    return rt


def get_client(eng: Engine) -> LLMClient:
    tier = eng.settings.get("ai.tier", "OFF")
    if tier == "OFF":
        raise AIUnavailable("AI is switched off")
    model = eng.settings.get("ai.model_path") or DEFAULT_MODELS.get(tier, "")
    return runtime(eng).client(model)


def status(eng: Engine) -> dict[str, Any]:
    from verismo_engine.ai import hardware

    rt = eng.extras.get("llm_runtime")
    return {
        "tier": eng.settings.get("ai.tier", "OFF"),
        "model_path": eng.settings.get("ai.model_path"),
        "hardware": hardware.detect(),
        "runtime": rt.status() if rt else {"running": False},
        "queue": eng.jobs.status(),
    }
