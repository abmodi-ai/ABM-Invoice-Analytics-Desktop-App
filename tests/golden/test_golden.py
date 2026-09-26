"""Golden-set acceptance gates (spec 10 Phase 1/2 and 11.1).

CI fails if HARD precision < 0.98, if PROBABLE+ recall < 0.90, or if PROBABLE precision < 0.70.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invoice_analytics.context import Engine

from .harness import run_golden

OUT = Path(__file__).parent / "out"


@pytest.mark.parametrize("seed", [42, 7])
def test_golden_thresholds(refdata_sample: Engine, seed: int) -> None:
    rep = run_golden(refdata_sample, seed=seed, n_invoices=1200, n_patients=600)
    OUT.mkdir(exist_ok=True)
    (OUT / f"golden_seed{seed}.md").write_text(rep.markdown())
    (OUT / f"golden_seed{seed}.json").write_text(json.dumps(rep.as_dict(), indent=1, default=list))
    assert rep.counts["rule_errors"] == 0
    assert rep.tier_precision["HARD"]["precision"] >= 0.98, rep.markdown()
    assert rep.recall_hard_exact >= 0.95, rep.markdown()
    assert rep.recall_probable_plus >= 0.90, rep.markdown()
    assert rep.tier_precision["PROBABLE"]["precision"] >= 0.70, rep.markdown()
    assert sum(rep.legit_false_positives.values()) == 0, rep.markdown()
    assert rep.identity["link_precision"] >= 0.995, rep.markdown()
    for kind, v in rep.per_type.items():
        assert v["recall"] >= 0.8, f"{kind}: {v}"


def test_golden_snapshot(refdata_sample: Engine) -> None:
    """Flag output for a fixed seed is snapshotted; any change needs an explicit update.

    Update with:  IA_UPDATE_SNAPSHOTS=1 uv run pytest tests/golden -k snapshot
    """
    import os

    run_golden(refdata_sample, seed=11, n_invoices=400, n_patients=250)
    rows = refdata_sample.db.query(
        "SELECT f.rule_id, f.tier, f.suppressed_by, i.invoice_number_raw AS num, i.invoice_date AS d"
        " FROM flags f JOIN invoices i ON i.id=f.subject_invoice_id WHERE f.active=1"
        " ORDER BY f.rule_id, num, d, f.tier, f.suppressed_by"
    )
    snap = [f"{r['rule_id']}|{r['tier']}|{r['suppressed_by'] or ''}|{r['num']}|{r['d']}" for r in rows]
    path = Path(__file__).parent / "snapshots" / "seed11.txt"
    if os.environ.get("IA_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(snap) + "\n")
    assert path.read_text().splitlines() == snap
