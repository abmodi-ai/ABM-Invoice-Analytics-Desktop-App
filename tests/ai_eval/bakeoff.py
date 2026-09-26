"""Model bake-off harness (spec 7.6).

    make ai-eval MODEL=/path/to/model.gguf          # starts llama-server for that model
    uv run python tests/ai_eval/bakeoff.py --base-url http://127.0.0.1:8080   # an already-running server

Evaluation sets (synthetic by default; replace with the labelled client sets when available,
same JSON shape, via --sets DIR):
  extraction: generated invoice PDFs with known header fields and lines    (default 200)
  triage:     labelled duplicate / legitimate flag pairs from synthgen      (default 300)
  nlq:        questions with expected result sets computed by reference SQL (default 100)
Metrics: field-level extraction accuracy, triage agreement with labels (Cohen's kappa),
invalid/uncited output rate, p50/p95 latency, peak RAM of llama-server. Output: markdown report.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("IA_EMBEDDER", "hashing")


def kappa(a: list[str], b: list[str]) -> float:
    labels = sorted(set(a) | set(b))
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pe = sum((a.count(lab) / n) * (b.count(lab) / n) for lab in labels)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


class RamSampler(threading.Thread):
    def __init__(self, pid: int | None) -> None:
        super().__init__(daemon=True)
        self.pid, self.peak, self.stop_ = pid, 0, threading.Event()

    def run(self) -> None:
        while not self.stop_.wait(0.5):
            try:
                if self.pid:
                    self.peak = max(self.peak, psutil.Process(self.pid).memory_info().rss)
            except psutil.Error:
                pass


def build_engine(tmp: Path) -> Any:
    from conftest import make_config
    from invoice_analytics.clinical.refdata import install_dev_sample
    from invoice_analytics.context import open_engine

    eng = open_engine(make_config(tmp))
    install_dev_sample(eng)
    return eng


def eval_extraction(eng: Any, n: int, rng: random.Random) -> dict[str, Any]:
    from integration.test_documents import make_pdf
    from invoice_analytics.ai.tasks.extract import extract_draft
    from invoice_analytics.ingest.service import ingest_bytes

    fields_ok = fields_total = invalid = 0
    lat: list[float] = []
    for i in range(n):
        lines = [
            (
                f"Item {k} {rng.choice(['gloves', 'masks', 'linen', 'toner', 'trays'])}",
                f"{rng.randint(10, 900)}.{rng.randint(0, 99):02d}",
            )
            for k in range(rng.randint(1, 4))
        ]
        total = f"{sum(float(a) for _, a in lines):.2f}"
        num = f"AX-{rng.randint(10000, 99999)}"
        date = f"{rng.randint(1, 12):02d}/{rng.randint(13, 28):02d}/2026"
        pdf = make_pdf(num, date, lines, total, label=rng.choice(["Ref", "Doc #", "Bill"]), scanned=i % 3 == 0)
        out = ingest_bytes(eng, f"x{i}.pdf", pdf, options={"_in_process": True}, detect=False)
        did = out.parse.meta.get("needs_review_draft_id")
        if not did:
            continue
        t = time.perf_counter()
        res = extract_draft(eng, did)
        lat.append(time.perf_counter() - t)
        if res["status"] != "OK":
            invalid += 1
            continue
        s = json.loads(eng.db.scalar("SELECT output FROM ai_suggestions WHERE id=?", (res["suggestion_id"],)))[
            "extraction"
        ]
        truth = {"invoice_number": num, "invoice_date": date, "total": total}
        for k, v in truth.items():
            fields_total += 1
            got = str(s["invoice"].get(k) or "").replace("$", "").replace(",", "")
            fields_ok += int(got == v.replace(",", ""))
    return {
        "documents": n,
        "field_accuracy": fields_ok / fields_total if fields_total else None,
        "invalid_rate": invalid / max(1, len(lat) + invalid),
        "p50_s": pct(lat, 0.5),
        "p95_s": pct(lat, 0.95),
    }


def eval_triage(eng: Any, n: int) -> dict[str, Any]:
    from golden.harness import _invoice_index, ingest_world
    from invoice_analytics.ai.agent.loop import triage_flag
    from invoice_analytics.scoring.pipeline import detect
    from synthgen.generator import Generator

    world = Generator(seed=99, n_invoices=max(600, n * 3), n_patients=400).build()
    ingest_world(eng, world)
    detect(eng)
    idx = _invoice_index(eng)
    dup_ids = {i for c in world.cases if c.duplicate for k in c.invoices for i in idx.get(k, [])}
    flags = eng.db.query(
        "SELECT id, subject_invoice_id FROM flags WHERE active=1 AND suppressed_by IS NULL"
        " AND tier IN ('WEAK','PROBABLE') ORDER BY id"
    )
    random.Random(3).shuffle(flags)
    flags = flags[:n]
    human, model, lat, invalid, uncited_shown = [], [], [], 0, 0
    for f in flags:
        truth = "DUPLICATE" if f["subject_invoice_id"] in dup_ids else "NOT_DUPLICATE"
        t = time.perf_counter()
        res = triage_flag(eng, f["id"])
        lat.append(time.perf_counter() - t)
        human.append(truth)
        model.append(res["verdict"] or "UNCERTAIN")
        if res["status"] != "OK":
            invalid += 1
        out = json.loads(eng.db.scalar("SELECT output FROM ai_suggestions WHERE id=?", (res["suggestion_id"],)) or "{}")
        if res["status"] == "OK" and not out.get("cited_evidence") and out.get("verdict") != "UNCERTAIN":
            uncited_shown += 1
    return {
        "pairs": len(flags),
        "kappa": kappa(human, model),
        "invalid_rate": invalid / max(1, len(flags)),
        "uncited_verdicts_shown_as_valid": uncited_shown,
        "p50_s": pct(lat, 0.5),
        "p95_s": pct(lat, 0.95),
    }


NLQ_SET = [
    ("How many open flags are there?", "SELECT COUNT(*) FROM v_flags WHERE status='OPEN'"),
    ("How many invoices are there in total?", "SELECT COUNT(*) FROM v_invoices"),
    ("Which rule has the most flags?", "SELECT rule_id FROM v_flags GROUP BY rule_id ORDER BY COUNT(*) DESC LIMIT 1"),
    (
        "What is the total amount at risk on hard flags in dollars?",
        "SELECT SUM(amount_at_risk_cents)/100.0 FROM v_flags WHERE tier='HARD'",
    ),
    ("How many vendors are there?", "SELECT COUNT(*) FROM v_parties WHERE party_type='VENDOR'"),
]


def eval_nlq(eng: Any, n: int) -> dict[str, Any]:
    from invoice_analytics.ai.tasks.nlq import ask, run_readonly

    correct, invalid, lat = 0, 0, []
    qs = (NLQ_SET * (n // len(NLQ_SET) + 1))[:n]
    for q, ref in qs:
        expected = run_readonly(eng, ref)["rows"]
        t = time.perf_counter()
        out = ask(eng, q)
        lat.append(time.perf_counter() - t)
        if out["status"] != "OK":
            invalid += 1
            continue
        got = out["result"]["rows"]
        norm = lambda rows: [[round(v, 2) if isinstance(v, float) else v for v in r] for r in rows]  # noqa: E731
        correct += int(norm(got) == norm(expected))
    return {
        "questions": n,
        "result_accuracy": correct / n if n else None,
        "invalid_rate": invalid / max(1, n),
        "p50_s": pct(lat, 0.5),
        "p95_s": pct(lat, 0.95),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path)
    ap.add_argument("--base-url")
    ap.add_argument("--llama-server", default=os.environ.get("IA_LLAMA_SERVER", "llama-server"))
    ap.add_argument("--n-extract", type=int, default=200)
    ap.add_argument("--n-triage", type=int, default=300)
    ap.add_argument("--n-nlq", type=int, default=100)
    ap.add_argument("--out", type=Path, default=ROOT / "tests" / "ai_eval" / "out")
    a = ap.parse_args(argv)
    proc = None
    if a.base_url:
        os.environ["IA_LLM_BASE_URL"] = a.base_url
    elif a.model:
        port = 18080
        key = "bakeoff"
        threads = max(1, (psutil.cpu_count(logical=False) or 2) - 1)
        proc = subprocess.Popen(
            [
                a.llama_server,
                "-m",
                str(a.model),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--api-key",
                key,
                "--threads",
                str(threads),
                "--ctx-size",
                "8192",
                "--jinja",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.environ["IA_LLM_BASE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["IA_LLM_API_KEY"] = key
        import httpx

        for _ in range(360):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
    else:
        ap.error("give --model or --base-url")
    ram = RamSampler(proc.pid if proc else None)
    ram.start()
    tmp = Path(tempfile.mkdtemp(prefix="ia-bakeoff-"))
    eng = build_engine(tmp)
    eng.settings.set("ai.tier", "LITE")
    rng = random.Random(7)
    try:
        report = {
            "model": str(a.model or a.base_url),
            "extraction": eval_extraction(eng, a.n_extract, rng),
            "nlq": eval_nlq(eng, a.n_nlq),
            "triage": eval_triage(eng, a.n_triage),
        }
    finally:
        ram.stop_.set()
        if proc:
            proc.terminate()
    report["peak_llama_ram_gb"] = round(ram.peak / 2**30, 2) if ram.peak else None
    a.out.mkdir(parents=True, exist_ok=True)
    name = a.model.stem if a.model else "server"
    md = [f"# AI bake-off: {report['model']}", "", "| task | metric | value |", "|---|---|---|"]
    for task in ("extraction", "triage", "nlq"):
        for k, v in report[task].items():
            md.append(f"| {task} | {k} | {v if not isinstance(v, float) else round(v, 3)} |")
    md.append(f"| all | peak llama-server RAM (GB) | {report['peak_llama_ram_gb']} |")
    md += [
        "",
        "Acceptance (spec Phase 4/5): extraction >= 0.97 digital / 0.88 scanned; triage kappa >= 0.6;",
        "0 uncited verdicts shown as valid; triage p95 < 90 s on Lite.",
    ]
    (a.out / f"bakeoff-{name}.md").write_text("\n".join(md) + "\n")
    (a.out / f"bakeoff-{name}.json").write_text(json.dumps(report, indent=1))
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
