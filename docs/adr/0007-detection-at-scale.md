# ADR 0007: Detection at 1.25M lines within memory budget

Status: accepted

Measured (Apple M-series, 18 cores; indicative, since the reference machine is an 8-core Windows
PC) on 1.43M synthetic lines / 576k invoices:

| | result | budget |
|---|---|---|
| full sweep | 39.7 s | < 60 s |
| incremental per invoice | p50 0.020 s, max 0.062 s | < 1 s |
| peak RSS, whole run | 2.35 GB | < 2.5 GB |

How:
- The DuckDB mirror is loaded once per process, streamed from SQLite in 50k-row chunks with all
  joins done in SQLite, and kept current incrementally on ingest.
- Incremental runs join only against candidate tables pre-restricted to the affected party
  clusters, shared identifiers and patient clusters (`inv_c` / `lines_c`).
- No OR-joins (INV-007 is a union of equi-joins).
- INV-008 sweeps date-sorted numpy arrays per party with a two-phase subset-sum, and resolves
  phase 2 in chunks of 5,000 prior invoices.
- CLN-008 fetches embeddings in 20k-pair chunks.
- Flags are persisted in 5,000-row batches with a (subject_id, last_run_id) index.
