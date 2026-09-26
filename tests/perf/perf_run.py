"""Performance budgets (spec 11.2) on synthetic data.

    uv run python tests/perf/perf_run.py --lines 250000
    uv run python tests/perf/perf_run.py --lines 1250000

Loads data straight through persist() (the budgets cover detection, not CSV parsing), then
measures: detection-store load, full sweep, incremental per invoice, and peak RSS.
Reference hardware is an 8-core Windows PC; numbers from other machines are indicative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("VERISMO_EMBEDDER", "hashing")

from conftest import make_config  # noqa: E402
from synthgen.generator import Generator, Invoice  # noqa: E402
from verismo_engine.clinical.refdata import install_dev_sample  # noqa: E402
from verismo_engine.context import open_engine  # noqa: E402
from verismo_engine.ingest.model import InvoiceIn, LineIn, ParseResult, PartyIn, PatientIn  # noqa: E402
from verismo_engine.ingest.persist import persist  # noqa: E402
from verismo_engine.scoring.pipeline import detect  # noqa: E402


def to_parse(invs: list[Invoice]) -> ParseResult:
    pr = ParseResult(method="CSV")
    for inv in invs:
        v = inv.vendor
        lines = []
        for n, li in enumerate(inv.lines, start=1):
            p = li.patient
            o = li.patient_override or {}
            pat = (
                PatientIn(
                    o.get("first", p.first), o.get("last", p.last), o.get("dob", p.dob), p.sex, p.zip, p.member_id
                )
                if p
                else None
            )
            lines.append(
                LineIn(
                    n,
                    pat,
                    li.dos,
                    li.dos,
                    li.code,
                    list(li.mods),
                    li.units,
                    li.charge,
                    rendering_npi=li.npi,
                    description=li.desc,
                )
            )
        pr.invoices.append(
            InvoiceIn(
                "AP",
                PartyIn(
                    inv.party_name_override or v.name,
                    "VENDOR",
                    None if inv.no_tax_id else v.tax_id,
                    None if inv.no_tax_id else v.npi,
                    v.address,
                ),
                inv.number,
                inv.date,
                inv.amount,
                inv.date,
                claim_frequency_code=inv.freq,
                original_invoice_ref=inv.orig_ref,
                status=inv.status,
                lines=lines,
            )
        )
    return pr


def build(lines: int, data: Path) -> dict[str, float]:
    """Phase 1 (separate process): generate and persist; writes the holdout batches to disk."""
    n_inv = int(lines / 2.45)
    t = time.perf_counter()
    world = Generator(
        seed=5,
        n_vendors=max(30, n_inv // 2000),
        n_patients=max(1200, n_inv // 4),
        n_invoices=n_inv,
        days=5 * 365,
        dup_rate=0.25,
    ).build()
    gen_s = time.perf_counter() - t
    print(f"generated {len(world.invoices)} invoices in {gen_s:.1f}s", flush=True)
    eng = open_engine(make_config(data))
    install_dev_sample(eng)
    batches: dict[tuple[str, str], list[Invoice]] = defaultdict(list)
    for inv in world.invoices:
        batches[(inv.date[:7], inv.vendor.code)].append(inv)
    keys = sorted(batches)
    t = time.perf_counter()
    for k in keys[:-20]:
        persist(
            eng,
            to_parse(batches[k]),
            sha256=hashlib.sha256(repr(k).encode()).hexdigest(),
            source_path=f"{k}.csv",
            mime="CSV",
            user_id=None,
        )
    persist_s = time.perf_counter() - t
    print(f"persisted in {persist_s:.1f}s", flush=True)
    import pickle

    (data / "holdout.pkl").write_bytes(pickle.dumps([(k, to_parse(batches[k])) for k in keys[-20:]]))
    eng.close()
    return {"generate_s": round(gen_s, 2), "persist_s": round(persist_s, 2)}


def measure(data: Path) -> dict[str, object]:
    """Phase 2 (fresh process): what the engine itself costs."""
    import pickle

    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    eng = open_engine(make_config(data))
    n_lines = eng.db.scalar("SELECT COUNT(*) FROM invoice_lines")
    t = time.perf_counter()
    store_stats = eng.store.load_all()
    store_s = time.perf_counter() - t
    rss_store = proc.memory_info().rss
    t = time.perf_counter()
    full = detect(eng)
    sweep_s = time.perf_counter() - t
    rss_sweep = proc.memory_info().rss
    print(f"store {store_s:.1f}s, sweep {sweep_s:.1f}s", flush=True)
    per_inv = []
    for k, pr in pickle.loads((data / "holdout.pkl").read_bytes()):
        t = time.perf_counter()
        res = persist(
            eng,
            pr,
            sha256=hashlib.sha256(repr(k).encode()).hexdigest(),
            source_path=f"{k}.csv",
            mime="CSV",
            user_id=None,
        )
        eng.store.upsert_invoices(res.invoice_ids)
        detect(eng, res.invoice_ids)
        per_inv.append((time.perf_counter() - t) / max(1, len(res.invoice_ids)))
    per_inv.sort()
    st = full.stats()
    out = {
        "lines": n_lines,
        "invoices": eng.db.scalar("SELECT COUNT(*) FROM invoices"),
        "store_load_s": round(store_s, 2),
        "store_stats": store_stats,
        "full_sweep_s": round(sweep_s, 2),
        "full_sweep_rule_timings": {k: round(v, 3) for k, v in full.timings.items()},
        "incremental_per_invoice_p50_s": round(per_inv[len(per_inv) // 2], 4),
        "incremental_per_invoice_max_s": round(per_inv[-1], 4),
        "rss_baseline_mb": rss0 // 2**20,
        "rss_after_store_load_mb": rss_store // 2**20,
        "rss_after_sweep_mb": rss_sweep // 2**20,
        "rss_peak_mb": getattr(proc.memory_info(), "peak_wset", 0) // 2**20 or None,
        "machine": {"physical_cores": psutil.cpu_count(logical=False), "platform": sys.platform},
        "flags_by_tier": st["by_tier"],
        "flags_by_rule": st["by_rule"],
        "rule_errors": st["errors"],
    }
    eng.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=250_000)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    ap.add_argument("--phase", choices=["all", "build", "measure"], default="all")
    ap.add_argument("--data", type=Path)
    a = ap.parse_args()
    data = a.data or Path(tempfile.mkdtemp(prefix="verismo-perf-"))
    if a.phase == "build":
        print(json.dumps(build(a.lines, data)))
        return 0
    if a.phase == "measure":
        print("RESULT " + json.dumps(measure(data), default=str))
        return 0
    import subprocess

    b = subprocess.run(
        [sys.executable, __file__, "--phase", "build", "--lines", str(a.lines), "--data", str(data)],
        check=True,
        capture_output=True,
        text=True,
    )
    built = json.loads(b.stdout.strip().splitlines()[-1])
    m = subprocess.run(
        [sys.executable, __file__, "--phase", "measure", "--data", str(data)],
        check=True,
        capture_output=True,
        text=True,
    )
    measured = json.loads(next(ln for ln in m.stdout.splitlines() if ln.startswith("RESULT "))[7:])
    report = {**built, **measured}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"perf_{a.lines}.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
