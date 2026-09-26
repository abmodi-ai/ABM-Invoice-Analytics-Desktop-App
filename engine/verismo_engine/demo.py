"""Demo data loader: synthetic invoices + sample reference data, then identity resolution and a
full sweep. Development and client demos only; never mixes with real data unless asked."""

from __future__ import annotations

from typing import Any

from verismo_engine.context import Engine


def load_demo(eng: Engine, n_invoices: int = 1500) -> dict[str, Any]:
    from synthgen.generator import Generator
    from synthgen.writers import CSV_MAPPING, render
    from verismo_engine.clinical.refdata import install_dev_sample
    from verismo_engine.ingest.service import ingest_bytes, save_template
    from verismo_engine.linkage.parties import suggest_merges
    from verismo_engine.linkage.patients import resolve_patients
    from verismo_engine.scoring.pipeline import detect

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
