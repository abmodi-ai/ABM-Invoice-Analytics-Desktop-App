"""Hardware detection and AI tier recommendation (spec 7.2)."""

from __future__ import annotations

from typing import Any

import psutil

TIERS: dict[str, dict[str, Any]] = {
    "OFF": {"min_ram_gb": 0, "needs_avx2": False, "min_cores": 0, "model": None, "approx_ram_gb": 0},
    "LITE": {
        "min_ram_gb": 15,
        "needs_avx2": True,
        "min_cores": 4,
        "model": "qwen3-4b-instruct-q4_k_m.gguf",
        "approx_ram_gb": 3,
    },
    "STANDARD": {
        "min_ram_gb": 15,
        "needs_avx2": True,
        "min_cores": 8,
        "model": "qwen3-8b-q4_k_m.gguf",
        "approx_ram_gb": 5.5,
    },
    "PLUS": {
        "min_ram_gb": 31,
        "needs_avx2": True,
        "min_cores": 8,
        "model": "qwen3-30b-a3b-q4_k_m.gguf",
        "approx_ram_gb": 18,
    },
}


def has_avx2() -> bool | None:
    try:
        import cpuinfo

        flags = set(cpuinfo.get_cpu_info().get("flags", []))
        if "avx2" in flags:
            return True
        arch = cpuinfo.get_cpu_info().get("arch", "")
        if "ARM" in str(arch).upper():
            return None  # AVX2 is an x86 concept; llama.cpp uses NEON on ARM
        return False
    except Exception:  # noqa: BLE001
        return None


def detect() -> dict[str, Any]:
    ram = psutil.virtual_memory().total / 2**30
    cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 1
    avx2 = has_avx2()
    eligible = []
    for name, t in TIERS.items():
        ok = ram >= t["min_ram_gb"] and cores >= t["min_cores"]
        if t["needs_avx2"] and avx2 is False:
            ok = False
        if ok:
            eligible.append(name)
    recommended = "LITE" if "LITE" in eligible else "OFF"
    return {
        "ram_gb": round(ram, 1),
        "physical_cores": cores,
        "avx2": avx2,
        "eligible_tiers": eligible,
        "recommended_tier": recommended,
        "llama_threads": max(1, cores - 1),
    }
