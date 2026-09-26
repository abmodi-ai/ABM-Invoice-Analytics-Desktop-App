"""synthgen CLI: write a synthetic dataset (documents + ground truth) to a folder.

uv run synthgen --out /tmp/synth --seed 42 --invoices 2500
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from synthgen.generator import Generator
from synthgen.writers import CSV_MAPPING, render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate labelled synthetic invoices and claims")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--vendors", type=int, default=30)
    ap.add_argument("--patients", type=int, default=1200)
    ap.add_argument("--invoices", type=int, default=2500)
    ap.add_argument("--days", type=int, default=540)
    a = ap.parse_args(argv)
    world = Generator(a.seed, a.vendors, a.patients, a.invoices, days=a.days).build()
    docs = render(world)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "docs").mkdir(exist_ok=True)
    order = []
    for d in docs:
        (a.out / "docs" / d.name).write_bytes(d.data)
        order.append({"name": d.name, "kind": d.kind, "date": d.date, "ingest_twice": d.ingest_twice})
    truth = {"seed": a.seed, "cases": [c.as_dict() for c in world.cases], "order": order, "csv_mapping": CSV_MAPPING}
    (a.out / "truth.json").write_text(json.dumps(truth, indent=1))
    print(f"wrote {len(docs)} documents, {len(world.invoices)} invoices, {len(world.cases)} labelled cases to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
