# Developer notes

## Prerequisites

uv (Python 3.12 is pinned and installed by uv), Node 22+, Rust stable (`rustup`), Tesseract 5 on
PATH for OCR tests. On Windows, WebView2 (preinstalled on Windows 11).

```bash
make setup      # uv sync + npm ci
make demo       # synthetic data + sample reference data into .ia-dev/data
make dev        # desktop app: Tauri spawns the engine via `uv run`, UI via Vite
```

Browser-only UI development (no Rust needed):

```bash
IA_DATA_DIR=.ia-dev/data IA_KEYSTORE=file IA_PORT=8765 \
  IA_TOKEN=<32+ chars> uv run python -m invoice_analytics
cd apps/desktop && VITE_ENGINE_URL=http://127.0.0.1:8765 VITE_ENGINE_TOKEN=<same> npx vite
```

`IA_KEYSTORE=file` stores keys unprotected. Use it for development and tests only. Production
uses DPAPI on Windows.

## Layout

```
engine/invoice_analytics/   api (FastAPI) · db (SQLCipher, migrations) · ingest (csv, x12, pdf, ocr)
                         normalize · rules (INV-*, CLN-*) · scoring (store, suppress, evidence, pipeline)
                         linkage (patients FS/EM, parties) · clinical (refdata) · ai (runtime, jobs,
                         guard, tasks, agent, prompts) · security (crypto, keystore, audit, users)
apps/desktop/            React UI (src/), Tauri shell (src-tauri/), Playwright (e2e/)
tools/synthgen/          labelled synthetic data     tools/license_check.py, make_model_package.py
refdata/                 CMS → signed .vref build    tests/  unit · integration · golden · perf · ai_eval
```

## Adding a rule

1. Implement a `Rule` subclass in `rules/invoice_rules.py` or `rules/clinical_rules.py`: a pure
   function of the DuckDB mirror (`inv`, `lines`, `ref_*`) returning `Candidate`s. Keep joins
   equi-joins, and use `ctx.q`/`ctx.qnp`: incremental runs transparently join against
   `inv_c`/`lines_c`.
2. Register it in `ALL_RULES`, and add defaults to `settings.DEFAULTS` (`rules.<ID>`).
3. Add ≥ 10 positive and ≥ 10 negative fixtures in `tests/fixtures/clinical/build_fixtures.py`.
4. Run `make golden`. If flags change intentionally, update the snapshot
   (`IA_UPDATE_SNAPSHOTS=1 uv run pytest tests/golden -k snapshot`) and explain why in the PR.

## Contracts

- The OpenAPI spec is the UI contract: `make openapi` regenerates `apps/desktop/src/api/schema.d.ts`.
- Migrations are append-only (`db/migrations/NNNN_name.sql`); an edited applied migration is refused.
- AI code writes only through `ai/guard.py:store_suggestion`, and a test enforces it.
- All HTTP client code lives in `ai/runtime.py` (loopback only), and a test enforces it.

## Tests

`make test` (≈ 1 min) · `make perf` (writes `tests/perf/out/`) · `make e2e` (needs `make demo` and a
running engine + Vite; credentials in `.ia-dev/demo-credentials.json`) ·
`make ai-eval MODEL=<gguf>`.
