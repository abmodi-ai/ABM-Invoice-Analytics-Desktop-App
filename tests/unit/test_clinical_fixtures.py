"""Clinical rule fixtures (spec Phase 3): every case runs through the full ingest + detection
pipeline on a fresh database with the sample reference data installed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verismo_engine.context import Engine
from verismo_engine.ingest.model import InvoiceIn, LineIn, ParseResult, PartyIn, PatientIn
from verismo_engine.ingest.persist import persist
from verismo_engine.ingest.service import run_incremental
from verismo_engine.rules.base import TIER_RANK

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "clinical" / "cases.json"


def _load() -> dict:
    if not FIX.exists():
        from tests.fixtures.clinical.build_fixtures import NPIS, PATIENTS, cases

        FIX.write_text(json.dumps({"patients": PATIENTS, "npis": NPIS, "cases": cases()}, indent=1))
    return json.loads(FIX.read_text())


DATA = _load()


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["id"] for c in DATA["cases"]])
def test_clinical_fixture(refdata_sample: Engine, case: dict) -> None:
    eng = refdata_sample
    for n, inv in enumerate(case["invoices"]):
        lines = []
        for k, li in enumerate(inv["lines"], start=1):
            f, l, dob = DATA["patients"][li["patient"]]
            lines.append(
                LineIn(
                    k,
                    PatientIn(f, l, dob, member_id=f"M-{li['patient']}"),
                    li["dos"],
                    li["dos"],
                    li["code"],
                    [m for m in li["mods"].split(",") if m],
                    float(li["units"]),
                    int(li["charge"]),
                    rendering_npi=DATA["npis"][li["npi"]],
                    description=li.get("desc") or None,
                )
            )
        pr = ParseResult(
            method="CSV",
            invoices=[
                InvoiceIn(
                    "AP",
                    PartyIn("Fixture Medical Group", "VENDOR", "12-3456789"),
                    inv["number"],
                    inv["date"],
                    claim_frequency_code=inv.get("freq"),
                    original_invoice_ref=inv.get("orig"),
                    lines=lines,
                )
            ],
        )
        res = persist(eng, pr, sha256=f"{case['id']}-{n}", source_path="fixture.csv", mime="CSV", user_id=None)
        det = run_incremental(eng, res, None)
        assert not det["errors"], det["errors"]
    flags = eng.db.query(
        "SELECT tier FROM flags WHERE rule_id=? AND active=1 AND suppressed_by IS NULL", (case["rule"],)
    )
    if case["expect"] == "fire":
        assert flags, f"{case['id']} ({case.get('note')}): expected {case['rule']} to fire"
        if case.get("tier"):
            best = max(flags, key=lambda f: TIER_RANK[f["tier"]])["tier"]
            assert best == case["tier"], f"{case['id']}: expected tier {case['tier']}, got {best}"
    else:
        strong = [f for f in flags if TIER_RANK[f["tier"]] >= TIER_RANK["WEAK"]]
        assert not strong, f"{case['id']} ({case.get('note')}): {case['rule']} fired {strong}"


def test_fixture_counts() -> None:
    from collections import Counter

    c = Counter((x["rule"], x["expect"]) for x in DATA["cases"])
    for rule in [f"CLN-00{i}" for i in range(1, 10)]:
        assert c[(rule, "fire")] >= 10 and c[(rule, "none")] >= 10, (rule, c[(rule, "fire")], c[(rule, "none")])


def test_flags_record_refdata_version(refdata_sample: Engine) -> None:
    eng = refdata_sample
    case = next(c for c in DATA["cases"] if c["rule"] == "CLN-004" and c["expect"] == "fire")
    test_clinical_fixture(eng, case)
    rv = eng.db.scalar("SELECT refdata_version FROM flags WHERE rule_id='CLN-004' LIMIT 1")
    assert rv and "MUE_PRACTITIONER=DEV-2026Q3" in rv
