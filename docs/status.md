# Build status against the implementation plan

What exists in this repository, how it was verified, and what still needs people, real data or
Windows hardware. Section numbers refer to [implementation-plan.md](implementation-plan.md).

## Verified in the build environment (macOS arm64, 18 cores)

| Area | Status | Evidence |
|---|---|---|
| §4 Data model | All tables, plus supporting tables: detection_runs, invoice_links, identity_suggestions, csv_mapping_templates, vendor_templates, extraction_drafts, document_blobs, settings_history | migrations 0001–0005 |
| §5.2 Normalization | All fields, property-tested (hypothesis). Found and fixed: `ẞ` casefolding, OCR-corrupted prefixes | `tests/unit/test_normalize.py` |
| §5.3 Identity | Party deterministic merges + scored suggestions; patient Fellegi-Sunter/EM with review queue, link/unlink, audit | `test_linkage.py`: auto-link precision 1.0 on the hard set |
| §5.4 Rules | INV-001…008, CLN-001…009, SUP-001…006 | 181 clinical fixtures (≥10 pos / ≥10 neg per CLN rule) |
| §5.5 Evidence | Spec schema + subject/counterpart views, suppressions considered, base tier | integration tests, UI |
| Golden set (synthetic) | HARD precision 1.000, PROBABLE precision 1.000, recall at PROBABLE+ 1.000, HARD recall for exact duplicates 1.000, 0 PROBABLE+ flags on 9 legitimate-repeat types, identity link precision 1.000 (seeds 42 and 7) | `tests/golden/out/*.md` |
| §6 Reference data | Signed `.vref` build, verify, import; effective dating by DOS; CMS format converters; editors | `test_refdata_build.py`, API tests |
| §7 AI | Runtime (lazy start, idle stop, sha256 + licence check, loopback only), tiers, job queue, T1–T4, write guard, prompts, bake-off harness | `test_ai.py`, `test_documents.py`, `test_security.py` against a scripted llama-server fake |
| §8 UI | All 11 screens, keyboard review (J/K, C/D/N, Enter/Esc), dark mode, auto-lock | Playwright `e2e/review-flow.spec.ts` against the real engine + demo data |
| §9 Security | See [security.md](security.md) | security test suite |
| §11.2 Performance | 1.43M lines: full sweep 39.7 s, incremental 0.02 s/invoice, peak RSS 2.35 GB | `tests/perf/out/`, ADR 0007 |
| §12 Packaging | Tauri 2 shell (supervision, token handshake, CSP, parent-watch); PyInstaller one-dir engine (274 MB) with a frozen end-to-end smoke test (`tools/frozen_smoke.py`: digital + scanned PDF via the OCR subprocess, CSV, sweep, audit); pre-migration backup on upgrade; MSI config | `cargo clippy -D warnings` clean; desktop app launched and reached sign-in |
| CI | Windows workflow: lint, license gates, tests, socket test, perf, frozen smoke test, SBOM, MSI artifact | `.github/workflows/ci.yml` (not yet run: no remote configured) |

Test totals: 306 Python tests (unit, property, fixtures, integration, security, golden), plus the
real-process network test, 5 Vitest tests and 1 Playwright end-to-end flow. Lint and types are
clean: ruff, black, mypy --strict (engine), eslint, tsc, and cargo clippy -D warnings. Both license
gates pass.

## Deliberate deviations (each has an ADR)

- INV-004 tiering and sequential-number guards; INV-003/004/005 skip disjoint-patient pairs: ADR 0003.
- Splink replaced by an in-house Fellegi-Sunter/EM (Splink pulls in GPL igraph), plus a
  conservative auto-link policy: ADR 0005. Party linkage uses fixed weights: ADR 0004.
- pytesseract replaced by direct Tesseract stdin/stdout (no temp files): ADR 0006.
- SUP-004 (recurring series) does not downgrade CLN-004/CLN-007: a series explains repeats, not
  exceeding an explicit cap.
- INV-008 requires a *unique* subset-sum match, and every part must share content (PO, patient/code
  lines, or descriptions) with the prior invoice. Without this, a subset-sum over 20 candidates
  with a $1 tolerance flags coincidences at volume.
- Name-only party matching refuses to merge records with different remit addresses when neither
  side has a tax ID or NPI.
- Money and counts on the dashboard and in reports are per **subject invoice**, not per flag
  (several rules usually fire on one duplicate). A review decision applies to the sibling flags on
  the same invoice pair by default (`apply_to_siblings`), each with its own review row and audit
  entry, so one duplicate is decided once.
- Background AI work is capped per detection run (`ai.max_background_jobs_per_run`, default 200,
  largest amounts first).

## Not done here: needs people, data or hardware

| Item | Why | What exists |
|---|---|---|
| Decisions D1–D8 confirmation | Product owner | Defaults implemented as specified |
| Real de-identified labelled data (Phase 2, 2,000 invoices) | Client | Golden harness accepts any world; metrics code ready |
| SME-written clinical fixtures + sign-off (Phase 3) | Certified coder | Fixture format and 181 engineering fixtures in `tests/fixtures/clinical/` |
| Real CMS reference data | Build machine with internet + quarterly URLs | `refdata/fetch_cms.py`, `build_bundle.py`, converters tested on CMS-shaped files. The dev sample is **illustrative, not CMS values** |
| Release signing keys | Release owner | `refdata/make_signing_key.py`; replace `TRUSTED_PUBLIC_KEYS` (currently the dev key) |
| Real-model bake-off (choose Lite/Standard defaults) | GGUF download + llama.cpp | `make ai-eval MODEL=…` harness; model packager `tools/make_model_package.py` |
| bge-small embedding model files | Build step (fastembed download to `models/embeddings`) | CLN-008 uses a deterministic hashing embedder until installed; evidence records which |
| MSI install on a clean offline Windows 11 VM; DPAPI on real Windows | Windows | CI builds the MSI and smoke-tests the frozen engine with `VERISMO_KEYSTORE=dpapi` |
| Code signing (OV/EV or Azure Trusted Signing) | Certificate | CI step placeholder |
| 8-hour soak test, UI p95 < 200 ms under LLM load, T2/T4 latency on Lite | Reference hardware + model | Perf harness; jobs never block requests by design |
| Pilot (2 weeks) | Users | — |

## Open questions for the client / SME

- **837 claim IDs (INV-001 on X12).** INV-001 (HARD) keys on CLM01, the patient control number.
  Some billing systems reuse it across a patient's claims. Synthetic data cannot show whether the
  client's systems do; confirm with the SME before trusting HARD precision on real 837s. If they
  do, key 837 INV-001 on CLM01 + first DOS or demote it to PROBABLE.
- **WEAK volume.** At 1.43M synthetic lines there were about 51k WEAK flags (mostly INV-005,
  same total within 45 days, and INV-004 on invoices days apart). WEAK is hidden by default, but
  the per-rule window/thresholds should be tuned on real data using the Threshold tuning report.
- **Decisions D1–D8** are implemented at their defaults; confirm before the pilot.
