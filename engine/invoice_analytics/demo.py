"""Demo data loader: synthetic invoices + sample reference data, then identity resolution and a
full sweep. Development and client demos only; never mixes with real data unless asked."""

from __future__ import annotations

from typing import Any

from invoice_analytics.context import Engine


def load_demo(eng: Engine, n_invoices: int = 1500) -> dict[str, Any]:
    from invoice_analytics.clinical.refdata import install_dev_sample
    from invoice_analytics.ingest.service import ingest_bytes, save_template
    from invoice_analytics.linkage.parties import suggest_merges
    from invoice_analytics.linkage.patients import resolve_patients
    from invoice_analytics.scoring.pipeline import detect
    from synthgen.generator import Generator
    from synthgen.writers import CSV_MAPPING, render

    install_dev_sample(eng)
    world = Generator(seed=2026, n_vendors=30, n_patients=max(400, n_invoices // 2), n_invoices=n_invoices).build()
    docs = render(world)
    from synthgen.writers import CSV_HEADER

    save_template(eng, "Synthetic vendor export", CSV_HEADER, CSV_MAPPING, {"direction": "AP"}, None)
    for d in docs:
        for _ in range(2 if d.ingest_twice else 1):
            ingest_bytes(eng, d.name, d.data, options={"direction": "AP"})
    ident = resolve_patients(eng)
    parties = suggest_merges(eng)
    res = detect(eng)
    return {"documents": len(docs), "identity": ident, "parties": parties, "detection": res.stats()}
