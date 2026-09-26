# Verismo Invoice Tracker: Implementation Plan

Audience: engineering team building the desktop application. Status: v1.0 draft for developer handoff. Scope: An offline Windows desktop app that manages invoices and finds duplicate billing, both duplicate invoices and duplicate charges for the same patient or treatment.

> This is the source specification the build follows. Section numbers referenced throughout the
> code and docs (for example "spec 5.4") point here. What was built against it, and every
> deliberate deviation, is tracked in [status.md](status.md).

## 0. TL;DR for the developer

- Detection is deterministic first. Most duplicates come from normalization, SQL blocking keys, fuzzy string matching, probabilistic record linkage and published CMS rule tables. None of that needs an LLM.
- The local LLM only works at the edges. It extracts fields from messy documents, explains flags, answers natural-language queries and triages ambiguous cases agentically. It proposes and never decides. It cannot write to the flags or reviews tables.
- The app must work fully with AI switched off. AI is an optional enrichment layer that runs in a background queue and never blocks the UI.
- Nothing leaves the machine. No outbound network calls. The database is encrypted at rest. Patient identifiers are keyed-hashed for matching.
- Build in phases. Each phase ships something usable. AI arrives in Phase 4, on top of an engine that already works.
- Reference hardware (minimum spec): Windows 10 22H2 or Windows 11, x86-64 CPU with AVX2, 8 cores recommended, 16 GB RAM, SSD, no GPU.

## 1. Goals and non-goals

### Goals

- G1. Ingest invoices and claims from CSV/Excel exports, X12 EDI (837P, 837I, 835) and PDFs, both digital and scanned.
- G2. Detect duplicate invoices in two directions:
  - AR: have we already billed this customer or payer for this?
  - AP: has this vendor already charged us for this invoice?
- G3. Detect duplicate or over-billed clinical charges: same patient, same service, same or overlapping date, rebills, unit limits exceeded, unbundled pairs, services inside a global surgical period, and frequency limits exceeded.
- G4. Give every flag a machine-readable, human-readable evidence trail.
- G5. Provide a review workflow (confirm / dismiss / needs info) that doubles as labelled training data.
- G6. Run fully offline on a 16 GB CPU-only Windows PC.
- G7. Use optional local AI for extraction, explanation, natural-language query and agentic triage.

### Non-goals (v1)

- Submitting claims to payers or processing payments.
- Multi-site, real-time sync or any cloud component.
- Replacing a certified coder's judgment. The app flags; people decide.
- Training or fine-tuning models. v1 uses off-the-shelf open-weight models.

## 2. Assumptions and decisions needed

These were open questions in the design discussion. The plan proceeds on the default assumption in each row. The product owner should confirm or change each one before Phase 1 starts.

| # | Decision | Default assumption | Impact if different |
|---|---|---|---|
| D1 | Primary direction: AR, AP or both | Both, with AP first | AR-only removes vendor alias work, and AP-only removes customer/payer entities |
| D2 | Input formats in real use | CSV/Excel + X12 837/835 + PDFs (about 30% scanned) | PDF-heavy input moves Phase 4 extraction earlier and adds 2–4 weeks |
| D3 | Volume | Up to 250k invoice lines per year, 5 years retained (about 1.25M lines) | Over 5M lines needs a DuckDB-first matching design and more RAM testing |
| D4 | Users | Single user per install, optional read-only second user | Concurrent multi-user needs a client-server design, because SQLite on a network share is not supported |
| D5 | Hardware floor | 16 GB, CPU-only, AVX2 | Machines without AVX2 must run with AI off |
| D6 | CPT descriptors | Show codes only until an AMA CPT licence is in place | With a licence, bundle descriptors in reference data |
| D7 | Regulatory posture | App handles PHI, and the operator treats it as HIPAA-covered | Drives encryption, audit and access-control requirements (Section 9) |
| D8 | Distribution | Signed installer delivered by download or USB; models shipped separately | Affects installer size (Section 12) |

## 3. Architecture

```
┌────────────────────────── Windows desktop (single install) ───────────────────────────┐
│                                                                                        │
│  ┌──────────────────────────┐   localhost HTTP + per-launch token   ┌───────────────┐  │
│  │  Tauri shell (Rust)      │ ────────────────────────────────────► │  Engine       │  │
│  │  React + TypeScript UI   │ ◄──────────────────────────────────── │  (Python)     │  │
│  │  - Review queue          │         JSON (OpenAPI contract)       │  FastAPI      │  │
│  │  - Ingest / search       │                                       │               │  │
│  │  - Settings / audit      │                                       │  ingest/      │  │
│  └─────────┬────────────────┘                                       │  normalize/   │  │
│            │ spawns + supervises sidecars                           │  rules/       │  │
│            │                                                        │  linkage/     │  │
│            ▼                                                        │  clinical/    │  │
│  ┌──────────────────────────┐   127.0.0.1, API key, lazy start      │  ai/ (jobs)   │  │
│  │  llama-server (llama.cpp)│ ◄──────────────────────────────────── │  db/          │  │
│  │  GGUF model, CPU only    │     OpenAI-compatible API             └──────┬────────┘  │
│  └──────────────────────────┘                                              │           │
│                                                                            ▼           │
│                              ┌─────────────────────────────────────────────────────┐   │
│                              │ SQLite + SQLCipher (system of record, encrypted)    │   │
│                              │ DuckDB (in-memory only, for bulk matching passes)   │   │
│                              │ Key protected by Windows DPAPI                      │   │
│                              └─────────────────────────────────────────────────────┘   │
│  Zero outbound network. All sidecars bind to 127.0.0.1 only.                           │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Technology choices

| Concern | Choice | Why | License |
|---|---|---|---|
| Desktop shell | Tauri 2 | About 10 MB binary and low RAM, which leaves memory for the model | MIT / Apache-2.0 |
| UI | React + TypeScript + Vite, TanStack Table/Query, a component lib such as shadcn/ui | Standard, fast to build | MIT |
| Engine | Python 3.12, FastAPI, Pydantic v2 | The best record-linkage, OCR and PDF ecosystem | MIT |
| Engine packaging | PyInstaller (one-dir) as a Tauri sidecar | Proven on Windows | GPL with bootloader exception, so it can ship with commercial apps |
| System of record | SQLite + SQLCipher (sqlcipher3) | Single file, encrypted at rest, zero admin | BSD-style |
| Bulk matching | DuckDB, in-memory only | Fast columnar joins, and the Splink backend | MIT |
| Fuzzy strings | rapidfuzz | C++ speed; Jaro-Winkler, Levenshtein, token ratios | MIT |
| Probabilistic linkage | Splink (DuckDB backend) | Fellegi-Sunter with EM-estimated m/u parameters, explainable | MIT |
| PDF text | pypdfium2 + pdfplumber | Tables and positions. Do not use PyMuPDF: it is AGPL | Apache/BSD, MIT |
| OCR | Tesseract 5 (bundled) via pytesseract | Offline, mature, easy to package | Apache-2.0 |
| Embeddings | bge-small-en-v1.5 via ONNX Runtime (fastembed) | About 130 MB, milliseconds per item on CPU | MIT / Apache-2.0 |
| LLM runtime | llama.cpp llama-server | CPU-optimized, GGUF, JSON-schema/grammar-constrained output, tool calling | MIT |
| SQL safety (NL query) | sqlglot | Parses and validates generated SQL before it runs | MIT |
| Migrations | Plain versioned SQL files + a small runner | Transparent and SQLCipher-friendly | n/a |
| Tests | pytest, hypothesis (property tests), Vitest, Playwright | Standard | MIT / MPL |

Licensing rule: every dependency must be MIT, BSD, Apache-2.0 or similarly permissive. No AGPL or GPL runtime dependencies. CI runs a license check (pip-licenses, license-checker) and fails the build on violations.

### 3.2 Process model and IPC

- On launch, Tauri spawns the engine sidecar and passes it a random port and a random 256-bit token through environment variables. The UI talks to the engine at `http://127.0.0.1:<port>` and sends the header `Authorization: Bearer <token>`. The engine rejects any other origin or token.
- The engine starts llama-server lazily on the first AI job, with `--host 127.0.0.1 --port <random> --api-key <random>`, and stops it after a configurable idle period (default 10 minutes) to free RAM.
- Tauri supervises both sidecars. If one crashes, Tauri restarts it with backoff and shows the error in the UI.
- Tauri's CSP only allows connect-src to the engine's loopback origin.

### 3.3 Repository layout

```
/apps/desktop/                 Tauri + React app
  src/                         React UI
  src-tauri/                   Rust shell, sidecar config, CSP
/engine/                       Python package: verismo_engine
  api/                         FastAPI routers (OpenAPI is the UI contract)
  db/                          connection, migrations/, repositories
  ingest/                      csv/, x12/, pdf/, ocr/, templates/
  normalize/                   amounts, dates, identifiers, names, codes
  rules/                       invoice-level rule implementations (INV-*)
  clinical/                    clinical rules (CLN-*), reference data access
  linkage/                     splink models, party + patient identity resolution
  scoring/                     tiering, suppression, evidence builder
  ai/                          runtime client, job queue, tasks/, agent/, prompts/
  security/                    crypto, hmac keys, dpapi, audit chain
/refdata/                      scripts that build signed reference-data bundles
/models/                       (gitignored) model files + manifest.json with sha256
/tools/synthgen/               synthetic invoice/claim generator with injected duplicates
/tests/                        unit/, integration/, golden/, perf/, e2e/
/docs/                         this plan, ADRs, user guide
```

## 4. Data model

All money is stored as integer cents. All dates are ISO YYYY-MM-DD. Every row has created_at and updated_at. Soft deletes only: PHI is never hard-deleted without an explicit admin retention action.

### 4.1 Core tables

- **parties**: vendors, customers and payers. id, party_type (VENDOR|CUSTOMER|PAYER), display_name, name_norm, tax_id_hmac, npi, remit_address_norm, cluster_id, active
- **party_aliases**: known alternate names and IDs that map to one party. id, party_id, alias_type (NAME|TAX_ID|NPI|ADDRESS), value_norm, source
- **patients**: id, patient_key (HMAC of normalized first|last|dob), first_name_enc, last_name_enc, dob, sex, cluster_id. `*_enc` columns hold values encrypted at the field level on top of SQLCipher (defense in depth for exports and backups).
- **patient_source_ids**: MRNs and member IDs per source. id, patient_id, source_party_id, id_type (MRN|MEMBER_ID|ACCOUNT), value_hmac
- **documents**: id, source_path, sha256, mime, page_count, ingest_method (CSV|X12|PDF_TEXT|OCR|LLM), ingest_status, ingest_confidence, ingested_at, ingested_by
- **invoices**: id, document_id, direction (AP|AR), party_id, invoice_number_raw, invoice_number_norm, invoice_date, due_date, total_cents, currency, po_number, claim_frequency_code (1|7|8|null), original_invoice_ref, status (OPEN|PAID|VOID|CREDIT), is_credit
- **invoice_lines**: id, invoice_id, line_no, patient_id, dos_from, dos_to, code_system (CPT|HCPCS|REV|OTHER), code, modifiers (JSON sorted array), modifier_1..4, units, charge_cents, allowed_cents, paid_cents, rendering_npi, place_of_service, revenue_code, dx_codes (JSON), description_raw, description_norm, description_embedding (BLOB, float32)

### 4.2 Detection and review tables

- **flags**: id, rule_id, rule_version, engine_version, refdata_version, subject_type (INVOICE|LINE), subject_id, counterpart_ids (JSON), tier (HARD|PROBABLE|WEAK|INFO), score (0–1), suppressed_by (rule_id|null), evidence (JSON), status (OPEN|CONFIRMED|DISMISSED|NEEDS_INFO), created_at. Unique on (rule_id, subject_type, subject_id, counterpart_ids_hash), so re-runs are idempotent.
- **reviews**: id, flag_id, reviewer_user_id, decision (CONFIRMED_DUPLICATE|NOT_DUPLICATE|NEEDS_INFO), reason_code, note, decided_at, recovered_cents
- **ai_suggestions**: id, target_type, target_id, task (EXTRACT|EXPLAIN|TRIAGE|NLQ), model_id, model_sha256, prompt_version, input_hash, output (JSON), trace (JSON), latency_ms, status (OK|INVALID|TIMEOUT|ERROR), created_at. The AI layer can write only to this table and to jobs.
- **jobs**: background queue. id, kind, payload (JSON), priority, status (QUEUED|RUNNING|DONE|FAILED|CANCELLED), attempts, error, created_at, started_at, finished_at

### 4.3 Reference data tables (versioned, read-only at runtime)

- ref_dataset_versions (dataset, version, effective_from, effective_to, sha256, imported_at)
- ref_ncci_ptp (column1_code, column2_code, effective_from, effective_to, modifier_indicator (0|1|9), setting (PRACTITIONER|HOSPITAL))
- ref_mue (code, mue_value, mai (1|2|3), setting, effective_from, effective_to)
- ref_global_days (code, global_days (000|010|090|XXX|YYY|ZZZ|MMM), effective_year), from the Medicare Physician Fee Schedule RVU file
- ref_frequency_limits (code, max_count, period (DAY|WEEK|MONTH|YEAR|LIFETIME), scope (PATIENT|PATIENT_PROVIDER), source, note), maintained by the client
- ref_modifiers (modifier, category (DISTINCT|REPEAT|BILATERAL|GLOBAL_BYPASS|OTHER), description)
- ref_recurring_series (code, typical_frequency, note), a whitelist for legitimate repeats such as dialysis, chemo cycles and therapy plans of care

### 4.4 Security and audit tables

- users (id, username, display_name, role (ADMIN|REVIEWER|VIEWER), password_hash (argon2id), active, last_login_at)
- audit_log (id, ts, user_id, action, entity_type, entity_id, before (JSON), after (JSON), prev_hash, hash). This table is append-only and hash-chained: hash = SHA256(prev_hash || canonical_json(row)). The integrity checker is exposed in the admin UI.
- settings (key, value, updated_by, updated_at)

## 5. Detection engine

### 5.1 Pipeline

```
ingest → validate → normalize → persist → [identity resolution] → rule passes → suppression → tiering → flags
                                                                                            └→ (optional) AI jobs: EXPLAIN / TRIAGE
```

- Incremental mode (default): when new invoices arrive, run every rule for the new records against history. Target: under 1 s per invoice without OCR or AI.
- Full sweep: run everything against everything, for example after a threshold change or a reference data update. Target: under 60 s for 1.25M lines on reference hardware.
- Every rule is a pure function of (records, reference data, config), so it is deterministic and reproducible. Each flag stores rule_version, engine_version and refdata_version.

### 5.2 Normalization spec (engine/normalize/)

Each function must have property-based tests with hypothesis.

| Field | Rules |
|---|---|
| Amounts | Parse currency strings to integer cents. (123.45) and trailing - mean negative. Reject ambiguous values. |
| Dates | Parse the common US formats to ISO. Store the original string. A date that could be read two ways is flagged for the user, never guessed. |
| Invoice number | Uppercase. Strip whitespace, -, _, /, . and #. Strip known prefixes per party (INV, INVOICE, NO). Strip leading zeros. Also store an OCR-folded variant (O→0, I→1, L→1, S→5, B→8, Z→2, G→6) for fuzzy rules. |
| Party names | Uppercase, remove punctuation, collapse whitespace, strip legal suffixes (INC, LLC, LLP, PC, PA, CORP, CO, LTD), expand common abbreviations (MED→MEDICAL, CTR→CENTER, ASSOC→ASSOCIATES). |
| Tax ID / NPI | Digits only. Validate the NPI Luhn check digit (with the 80840 prefix). Keyed-hash tax IDs with HMAC. |
| Patient name | Uppercase, strip diacritics, drop suffixes (JR, SR, II, III), split compound last names and store tokens. Also store a phonetic key (Double Metaphone). |
| Codes | Uppercase, trim. Validate format: CPT is `\d{4}[0-9A-Z]` and HCPCS II is `[A-V]\d{4}`. Modifiers become a sorted set. |
| Descriptions | Lowercase, expand a curated abbreviation table (pt→physical therapy in context, inj→injection, eval→evaluation), remove stopwords, then compute the embedding. |

### 5.3 Identity resolution (engine/linkage/)

- Parties: deterministic merges on an exact tax ID HMAC or NPI. Then a Splink model on name_norm (Jaro-Winkler), remittance address and phone. Merges above the threshold are suggested to an admin, never applied automatically. Merge and unmerge are both audited.
- Patients: a Splink model on last name (JW + phonetic), first name (JW + nickname table), DOB (exact, transposed day/month, ±1 year typo), sex, ZIP and source IDs. Output is a cluster_id. Auto-link only above a high threshold (target precision ≥ 0.995). Pairs in the middle band go to an identity review queue.
- DuckDB runs in-memory only. It is loaded from SQLite through Arrow and disk spilling is disabled, so no plaintext PHI temp files are written.

### 5.4 Rule catalog

Tier meanings:

- HARD: almost certainly a duplicate. Recommend blocking payment or billing.
- PROBABLE: needs review.
- WEAK: logged and visible on request; triggers AI triage if enabled.
- INFO: context only.

All thresholds live in settings, are editable by admins and are versioned.

#### Invoice-level rules (AP and AR)

| ID | Rule | Default tier |
|---|---|---|
| INV-001 | Same party.cluster_id + same invoice_number_norm | HARD |
| INV-002 | Same document sha256 (same file ingested twice) | HARD |
| INV-003 | Same party + same total_cents + same invoice_date, different invoice number | PROBABLE |
| INV-004 | Same party + OCR-folded invoice numbers with Damerau-Levenshtein ≤ 1 or JW ≥ 0.92 + total within ±$0.05 | PROBABLE |
| INV-005 | Same party + same total_cents within N days (default 45) | WEAK |
| INV-006 | Line-set overlap: ≥ 80% of lines on invoice B match lines on an earlier invoice A by (patient, DOS, code, units, charge). This is a rebill under a new number. | PROBABLE (100% overlap: HARD) |
| INV-007 | Different party records that share a tax ID, NPI or remittance address, and INV-001 or INV-003 would match across them | Escalate one tier |
| INV-008 | Split billing: 2–4 invoices from the same party within 30 days whose totals sum to a prior invoice total within ±$1. Bounded subset-sum with a cap on candidates per party per window. | WEAK |

#### Line-level clinical rules

| ID | Rule | Default tier |
|---|---|---|
| CLN-001 | Exact duplicate line across different invoices: same patient cluster + DOS + code + modifier set + units + rendering NPI | HARD |
| CLN-002 | Same patient + DOS + code with different modifiers, where no modifier is in DISTINCT (59, XE, XS, XP, XU), REPEAT (76, 77, 91) or BILATERAL (50, RT/LT pair) | PROBABLE |
| CLN-003 | Same patient + code + DOS within ±1 day, different rendering NPI in the same party | WEAK |
| CLN-004 | MUE exceeded: total units per patient + code + DOS across all invoices exceeds ref_mue.mue_value. MAI 1 checks per claim line; MAI 2 and 3 check per date of service across claims. | PROBABLE (MAI 2: HARD) |
| CLN-005 | NCCI PTP pair: same patient + DOS + provider bills both column-1 and column-2 codes. Modifier indicator 0 → PROBABLE. Indicator 1 with no NCCI-associated modifier on the column-2 line → PROBABLE. Indicator 1 with a bypass modifier → INFO. | PROBABLE |
| CLN-006 | Global period: E/M or procedure by the same provider within the global days (010 or 090) after a surgery, without modifier 24, 25, 57, 58, 78 or 79 | PROBABLE |
| CLN-007 | Frequency limit in ref_frequency_limits exceeded | PROBABLE |
| CLN-008 | Semantic duplicate: same patient + DOS, description embedding cosine ≥ 0.90, and codes differ or are missing | WEAK (sent to AI triage) |
| CLN-009 | Rebill without correction: a line matches a previously billed or paid line, and the new claim's frequency code is not 7 or 8 | PROBABLE |

#### Suppression rules (applied after detection)

| ID | Suppression | Effect |
|---|---|---|
| SUP-001 | The counterpart is a credit or negative invoice that nets out the original | Suppress |
| SUP-002 | Frequency code 7 (replacement) references the original, which is superseded | Suppress; link the invoices |
| SUP-003 | Frequency code 8 (void) references the original | Suppress; mark the original VOID |
| SUP-004 | Code is in ref_recurring_series and the interval matches its typical frequency | Downgrade one tier |
| SUP-005 | A reviewer previously marked the same pair NOT_DUPLICATE | Suppress; keep for audit |
| SUP-006 | A REPEAT or BILATERAL modifier is correctly applied | Downgrade to INFO |

### 5.5 Scoring and evidence

- The final tier is the highest tier from all rules that fired on the subject, after suppressions.
- score comes from the Splink match probability where a fuzzy rule contributed, otherwise from a fixed per-rule base score. It is used to order the review queue, not to decide the tier.
- Evidence schema, which the UI renders and the AI layer consumes:

```json
{
  "rule_id": "CLN-001",
  "summary_template": "Same patient, date of service, code {code} and units already billed on invoice {other_invoice}.",
  "matched_fields": [
    {"field": "patient_cluster", "a": "P-1042", "b": "P-1042", "method": "exact"},
    {"field": "dos", "a": "2026-03-04", "b": "2026-03-04", "method": "exact"},
    {"field": "code", "a": "97110", "b": "97110", "method": "exact"},
    {"field": "invoice_number", "a": "INV-0045", "b": "INV-0054", "method": "damerau", "value": 1}
  ],
  "differing_fields": [{"field": "invoice_date", "a": "2026-03-10", "b": "2026-04-02"}],
  "refdata": [{"dataset": "MUE", "version": "2026Q3", "row": {"code": "97110", "mue": 4, "mai": 3}}],
  "suppressions_considered": ["SUP-002: no frequency code 7 present"],
  "amount_at_risk_cents": 12500
}
```

## 6. Reference data management

- Sources: CMS NCCI PTP edits (practitioner and hospital), CMS MUE tables, the Medicare PFS RVU file (global days), HCPCS Level II (public), and client-maintained frequency limits and recurring-series tables.
- Build: /refdata scripts download the files on an internet-connected build machine, normalize them and produce a signed bundle (.vref): a zip of CSV files plus manifest.json (dataset, version, effective dates, sha256 values) with an Ed25519 signature.
- Install: an admin imports the bundle through the UI. The engine checks the signature with the public key embedded in the app, checks hashes, loads it in one transaction and records ref_dataset_versions.
- Effective dating: rules look up reference rows using the line's DOS, not today's date, so a historical re-run uses the rules that applied at the time.
- CPT descriptors: not shipped until an AMA licence is confirmed (D6). Codes work without descriptors.

## 7. Local AI layer

### 7.1 Principles

- AI proposes and people or rules decide. The AI writes only to ai_suggestions and jobs.
- Every output is schema-constrained. Use llama-server's json_schema / grammar parameter. Outputs that fail validation are stored as INVALID and never shown as fact.
- Deterministic settings: temperature 0, fixed seed, versioned prompts stored in the repo under engine/ai/prompts/.
- Asynchronous only. No UI action waits on an LLM response. Results appear when ready.
- Graceful degradation. Every feature has a non-AI path. AI Off is a supported, tested mode.

### 7.2 Runtime and hardware tiers

On first run, the app checks RAM, core count and AVX2, then offers a tier. The user can change it later in Settings.

| Tier | Requirement | Default model (GGUF, Q4_K_M) | Approx. RAM | Approx. speed on 8-core CPU |
|---|---|---|---|---|
| Off | Any machine, including no AVX2 | none | 0 | n/a |
| Lite (default) | 16 GB, AVX2 | Qwen3 4B Instruct | ~3 GB | ~15–25 tok/s |
| Standard | 16 GB, AVX2, 8+ cores | Qwen3 8B | ~5.5 GB | ~8–12 tok/s |
| Plus | 32 GB+ | Qwen3-30B-A3B (MoE) | ~18 GB | ~15–20 tok/s |

- Alternates to include in the bake-off: Phi-4-mini (MIT) and IBM Granite 8B-class (Apache-2.0). Final defaults are chosen by the model bake-off harness (Section 7.6), not by this table.
- Only models with permissive licences (Apache-2.0 or MIT) are eligible for bundling.
- llama-server flags: `--threads <physical cores − 1> --ctx-size 8192 --host 127.0.0.1 --api-key <random> --jinja` (for tool calling). Load one model at a time.
- Model files are verified against models/manifest.json (sha256) before loading.

### 7.3 AI tasks

| Task | Trigger | Input | Output schema | Guardrails |
|---|---|---|---|---|
| T1 Extract | PDF/OCR ingest where template and heuristic extraction confidence is below threshold | Page text + layout blocks | Invoice + InvoiceLine[] with a confidence per field | Schema-constrained. Arithmetic cross-check: line totals must sum to the invoice total. Low-confidence fields are highlighted for a person to correct. Corrections are saved as vendor templates. |
| T2 Explain | A flag is created (PROBABLE+) or opened in the UI | Flag evidence JSON only | `{ "explanation": string, "key_differences": string[] }` | May only restate the evidence given. A post-check confirms every code, amount and date in the text appears in the evidence, otherwise the output is discarded. |
| T3 Ask (NL→SQL) | User types a question in Search | Question + curated schema of read-only views | `{ "sql": string, "explanation": string }` | Parse with sqlglot: single SELECT only, whitelisted views only, no PRAGMA or ATTACH, LIMIT enforced. Runs on a separate read-only connection with a timeout. Shows the SQL to the user. |
| T4 Triage (agent) | WEAK or PROBABLE flags, or a user clicks Investigate | Flag ID | Verdict (Section 7.4) | Read-only tools, at most 8 tool calls, a 120 s time budget, and citations validated against the trace |

### 7.4 Agentic triage design

Write a small, auditable agent loop (about 300 lines) in engine/ai/agent/. Do not adopt a heavyweight agent framework. Auditability and control matter more than features here.

Tools (all read-only, all return compact JSON, each with a Pydantic schema):

```
get_flag(flag_id)
get_invoice(invoice_id)
search_invoices(party_id?, patient_cluster?, code?, dos_from?, dos_to?, amount_cents?, limit<=20)
get_patient_service_history(patient_cluster, dos_from, dos_to, code?)
lookup_ncci_ptp(code_a, code_b, dos)
lookup_mue(code, dos)
lookup_global_days(code, dos)
check_modifiers(code, modifiers[])          -> categories + NCCI bypass eligibility
get_prior_reviews(subject_ids[])            -> previous human decisions on similar pairs
diff_invoices(invoice_a, invoice_b)         -> field-level diff
```

Loop:

1. The system prompt states the role, rules and output schema. The user message holds the flag evidence.
2. The model calls tools, and the engine executes them and returns results, up to 8 calls.
3. The model emits a Verdict:

```json
{
  "verdict": "DUPLICATE | NOT_DUPLICATE | UNCERTAIN",
  "confidence": 0.0,
  "rationale": "string",
  "cited_evidence": [{"tool_call_id": "tc_3", "field": "units", "value": "6"}],
  "recommended_action": "BLOCK | REVIEW | DISMISS"
}
```

4. Validator: every cited_evidence item must exist in the tool-call trace. If any citation is missing, set the verdict to UNCERTAIN and store the output as INVALID.
5. The full trace (prompts, tool calls, results, output) is stored in ai_suggestions.trace and viewable in the UI.
6. The verdict is shown on the review card as a recommendation. The reviewer's decision is what counts, and it is logged as a label.

### 7.5 Job queue

- jobs table + a worker thread pool in the engine. Concurrency is 1 for LLM jobs (CPU-bound) and N for others.
- Priority order: user-initiated (Investigate, Ask) > EXPLAIN for flags currently open in the UI > background TRIAGE > background EXPLAIN.
- Jobs can be cancelled. Retries use backoff, up to 2 attempts. The UI shows the queue depth and the current job.

### 7.6 Model bake-off harness (tests/ai_eval/)

- Evaluation sets: 200 labelled extraction documents, 300 labelled triage pairs, and 100 NL questions with expected result sets.
- Metrics: field-level extraction accuracy, triage agreement with people (Cohen's κ), rate of invalid or uncited outputs, p50/p95 latency and peak RAM on reference hardware.
- Run it with `make ai-eval MODEL=<gguf>` to produce a markdown report. Re-run whenever the model, prompts or llama.cpp version change.

## 8. User interface

| Screen | Key functions |
|---|---|
| Dashboard | Open flags by tier, amount at risk, amount recovered (from reviews), top vendors by duplicate rate, AI queue status |
| Ingest | Drag and drop files or a watched folder. Per-file status. CSV column-mapping wizard with saved templates per source. Extraction correction view (PDF on the left, fields on the right, low-confidence fields highlighted). |
| Review queue | Filter by tier, rule, party, date and amount. Side-by-side diff of subject and counterpart, with matched fields in green and differing fields in amber. Evidence list, AI explanation, AI verdict with an expandable trace, and Confirm / Dismiss / Needs info with a reason code. Keyboard-first: J/K to move, C/D/N to decide. |
| Invoice detail | Header, lines, source document preview, linked flags, history |
| Search / Ask | Structured filters, plus the natural-language box (shows the generated SQL; AI tiers only) |
| Parties and patients | Identity clusters, suggested merges, merge/unmerge with audit |
| Rules | Enable or disable rules, edit thresholds, preview how many flags a change would add or remove before applying it |
| Reference data | Installed dataset versions, import a signed bundle |
| Reports | Duplicates found and recovered by period and vendor, review throughput, rule precision (confirmed ÷ reviewed). Export to CSV or PDF. |
| Audit log | Filterable, with an integrity check button |
| Settings | AI tier, model path, watched folders, users and roles, backup and restore |

Accessibility: full keyboard navigation, WCAG AA contrast, and usable at 125% and 150% Windows scaling.

## 9. Security, privacy and compliance

| Control | Implementation |
|---|---|
| Encryption at rest | SQLCipher (AES-256). The 256-bit DB key is generated at install and protected with Windows DPAPI (user scope) or Credential Manager. Optional admin recovery key is printed once at setup. |
| Field-level encryption | Patient names are encrypted with a separate key, so exports and backups do not expose them by accident |
| Pseudonymous matching | Patient keys and identifiers are HMAC-SHA256 with an install secret. Matching never needs plaintext identifiers in indexes. |
| No network | No outbound calls from any process. Tauri CSP is locked to loopback. Sidecars bind to 127.0.0.1 only. No telemetry and no auto-update over the network. The installer can optionally add a Windows Firewall block rule. An automated CI test runs the app and fails if any non-loopback socket is opened. |
| Local API security | Random port and a random bearer token on each launch. Origin check. Requests from any other token are rejected. |
| Access control | Local users with argon2id password hashes. Roles: ADMIN, REVIEWER, VIEWER. Auto-lock after 15 minutes idle. Optional Windows-account sign-on in a later phase. |
| Audit | Hash-chained, append-only audit_log covering logins, views of PHI records, decisions, merges, settings changes, exports and imports |
| Logging | Structured logs with no PHI. IDs only; a redaction filter is enforced by a unit test. Logs rotate. |
| Backups | Encrypted backup file (same key or admin recovery key). Restore is tested in CI. |
| Temp files | No plaintext PHI temp files: DuckDB in-memory with spilling disabled; OCR images processed in memory and wiped. |
| Supply chain | Pinned dependencies with hashes, license scan, SBOM (CycloneDX) generated per release, signed binaries |
| HIPAA mapping | Access control §164.312(a), audit controls §164.312(b), integrity §164.312(c) and person authentication §164.312(d) are addressed above. Transmission security §164.312(e) does not apply because nothing is transmitted. Document these in /docs/security.md. |

## 10. Delivery phases

Estimates assume 2 engineers (one full-stack TypeScript/Rust, one Python/data), plus a part-time billing/coding SME (a certified coder is critical for Phases 2–3) and part-time QA. With one engineer, multiply by about 1.8.

### Phase 0: Foundations (2 weeks)

Tasks

- Monorepo scaffold as in Section 3.3. Lint and format (ruff, black, mypy strict; eslint, prettier; clippy).
- CI on GitHub Actions Windows runners: test, license check, build the installer as an artifact.
- Tauri shell that launches the engine sidecar with a port and token. Health endpoint. Crash supervision.
- SQLCipher DB, DPAPI key storage, migration runner, first migrations for Section 4.
- tools/synthgen: a synthetic generator for parties, patients, invoices and lines, with labelled injected duplicates (exact resubmit, OCR typos, renumbered rebill, split, rebill without frequency code, MUE overage, PTP pair) and labelled legitimate repeats (therapy series, bilateral, modifier 76/91).
- Audit log with hash chain. Users and roles. Login screen.

Acceptance criteria

- A signed or unsigned MSI builds in CI and installs and launches on a clean, offline Windows 11 VM.
- The DB file cannot be opened with plain sqlite3.
- The network test passes: no non-loopback sockets.

### Phase 1: Ingest and exact dedup MVP (4 weeks)

Tasks

- CSV/Excel importer with a column-mapping wizard and saved templates.
- X12 837P/837I/835 parser. Write a thin segment parser (ISA/GS/ST envelope, loops 2000A–2400, CLM, SV1/SV2, DTP, NM1, REF), and validate it against sample files.
- Full normalization library (Section 5.2) with property tests.
- Rules INV-001, INV-002, INV-003 and CLN-001, plus SUP-001 to SUP-003 and SUP-005.
- Evidence builder. Review queue UI with side-by-side diff and keyboard decisions.
- Dashboard v1. CSV export of flags.

Acceptance criteria

- On the synthetic golden set: HARD-tier precision ≥ 0.98 and recall ≥ 0.95 for injected exact duplicates.
- Incremental check < 1 s per invoice. Full sweep of 250k lines < 10 s.
- Every decision is persisted and appears in the audit log.
- Demo milestone: the client imports a real export and reviews the flags.

### Phase 2: Fuzzy matching and identity resolution (4 weeks)

Tasks

- Party alias resolution and suggested merges with a UI.
- Patient identity resolution with Splink and an identity review queue.
- Rules INV-004 to INV-008, CLN-002, CLN-003 and CLN-009. SUP-004 and SUP-006.
- Description embeddings (bge-small via ONNX) and CLN-008.
- Rules settings UI with a flag-count impact preview.
- Start collecting de-identified real labelled data from the client (target: 2,000 invoices with known outcomes).

Acceptance criteria

- Golden set: recall ≥ 0.90 at PROBABLE or above across all injected duplicate types. PROBABLE precision ≥ 0.70.
- Patient auto-link precision ≥ 0.995 on the labelled identity set.
- Full sweep of 1.25M lines < 60 s. Peak engine RAM < 2.5 GB.

### Phase 3: Clinical rules and reference data (4 weeks)

Tasks

- /refdata build scripts for NCCI PTP, MUE, PFS global days and HCPCS. Signed .vref bundles. Import UI with signature check.
- Effective-dated lookups by DOS.
- Rules CLN-004 to CLN-007, frequency limits editor and recurring-series editor.
- The SME writes a fixture per rule from real-world scenarios (at least 10 positive and 10 negative cases each).

Acceptance criteria

- 100% of rule fixtures pass. Each flag records refdata_version.
- Importing a new quarterly bundle changes behavior without a code release.
- The SME signs off on false-positive behavior for the modifier and global-period logic.

### Phase 4: Documents and local AI foundation (5 weeks)

Tasks

- PDF text extraction (pypdfium2 + pdfplumber), Tesseract OCR for scanned pages, layout heuristics, and vendor templates learned from corrections.
- Extraction correction UI.
- llama-server sidecar management (lazy start, idle stop, health, sha256 check), job queue and hardware tier detection.
- AI task T1 (Extract) with an arithmetic cross-check, and T2 (Explain) with a post-check.
- Model bake-off harness. Choose the default Lite and Standard models from the results.
- AI Off mode tested end to end.

Acceptance criteria

- Extraction field accuracy ≥ 97% on digital PDFs and ≥ 88% on scanned PDFs on the test corpus (after template learning).
- No UI interaction waits on the LLM. UI p95 response < 200 ms while an LLM job is running.
- Explanations pass the post-check in ≥ 98% of cases, and failures are discarded rather than shown.
- On a 16 GB reference machine with the Lite model loaded, total app memory is < 5 GB.

### Phase 5: Agentic triage and natural-language query (4 weeks)

Tasks

- Tool layer (Section 7.4), agent loop, citation validator, and trace viewer in the review card.
- T3 NL→SQL with curated read-only views, sqlglot validation and a read-only connection.
- Reviewer feedback capture as labels. Threshold tuning report (per rule precision and recall from real reviews, with suggested threshold changes for an admin to apply).

Acceptance criteria

- Agent verdicts on the held-out labelled set: κ ≥ 0.6 against human reviewers, 0 uncited verdicts shown as valid, p95 latency < 90 s on Lite.
- The NL→SQL fuzz test (500 adversarial prompts) produces 0 non-SELECT executions and 0 access outside the whitelisted views.
- Triage demonstrably reduces reviewer time per WEAK flag (measured in the pilot).

### Phase 6: Hardening and release (3 weeks)

Tasks

- Code signing (OV/EV certificate or Azure Trusted Signing). MSI installer. Separate model package (see Section 12).
- Backup and restore. Data retention tools for admins.
- Performance tuning against budgets. Memory soak test (8 hours of continuous ingest + AI).
- Security review: local attack surface, token handling, SQL injection, path traversal on import, and malicious PDFs (run the parser in a subprocess with a timeout).
- User guide, admin guide, and a runbook for reference data updates.
- Pilot: 2 weeks with real users and data. Fix the issues it finds.

Acceptance criteria

- All performance and security budgets met (Section 11).
- The pilot runs with no data-loss or crash-loop issues, and users confirm the review queue precision is acceptable.
- Release checklist complete, SBOM published internally, installer signed.

### Timeline summary

| Phase | Weeks | Cumulative |
|---|---|---|
| 0 Foundations | 2 | 2 |
| 1 Ingest + exact dedup MVP | 4 | 6 |
| 2 Fuzzy + identity | 4 | 10 |
| 3 Clinical rules + ref data | 4 | 14 |
| 4 Documents + local AI | 5 | 19 |
| 5 Agentic triage + NL query | 4 | 23 |
| 6 Hardening + release | 3 | 26 |

Phases 1–3 can ship to users as v0.x without any AI. Phases 4–5 add AI as an enhancement.

## 11. Quality, testing and budgets

### 11.1 Test strategy

- Unit tests: every normalizer (property-based), every rule (fixtures), every suppression, and every AI output validator.
- Golden datasets:
  - Synthetic: produced by tools/synthgen with a fixed seed and labelled ground truth. Runs in CI.
  - Real: de-identified client data with known outcomes. Stored encrypted outside the repo and run in a manual or nightly job on a secured machine.
- Metrics per rule and per tier: precision, recall and F1. CI fails if HARD precision drops below 0.98 or overall recall regresses by more than 2 points.
- Snapshot tests: flag output for the golden set is snapshotted, and any change needs an explicit update.
- Integration and e2e tests: Playwright drives the Tauri app through ingest → review → report.
- AI eval harness as in Section 7.6.
- Security tests: network-socket test, log PHI redaction test, NL→SQL fuzzing, malformed file fuzzing (CSV, X12, PDF).

### 11.2 Performance budgets (reference hardware)

| Operation | Budget |
|---|---|
| App cold start to usable UI | < 4 s |
| Incremental dedup per invoice (no OCR/AI) | < 1 s |
| Full sweep, 1.25M lines | < 60 s |
| Digital PDF ingest per page | < 1 s |
| OCR per scanned page | < 6 s |
| UI interaction p95 (during LLM job) | < 200 ms |
| T2 Explain (Lite) | < 30 s |
| T4 Triage (Lite) p95 | < 90 s |
| RAM: app without model | < 800 MB |
| RAM: app + Lite model | < 5 GB |

### 11.3 Definition of Done (every feature)

- Tests written and passing, and coverage of new engine code ≥ 85%.
- Audit events emitted for any state change.
- No PHI in logs (the redaction test passes).
- Works with AI Off.
- Documentation updated (user guide and/or ADR).
- Reviewed by a second engineer. Clinical rules are also reviewed by the SME.

## 12. Packaging and deployment

- Installer: MSI (WiX, via Tauri bundler), per-machine install. Includes the Tauri app, the engine sidecar, Tesseract and the embedding model. About 250–350 MB.
- Model package: distributed separately as verismo-models-<tier>.zip with manifest.json (sha256) because a GGUF file is 2.5–18 GB. It is installed from Settings → AI → Import model package. The app verifies hashes before use.
- Updates: fully offline. New MSI versions are installed over the old one, and DB migrations run automatically with a backup taken first. Reference data comes as signed .vref bundles.
- Code signing: all executables and the MSI are signed to avoid SmartScreen blocks.
- Data location: %PROGRAMDATA%\Verismo\ for shared reference data and models, %LOCALAPPDATA%\Verismo\ for the user DB and logs, and a configurable backup folder.
- Uninstall: keeps user data unless the user chooses Remove all data, which securely deletes the DB and keys.

## 13. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| High false-positive rate from legitimate repeats makes users abandon the tool | High | High | Suppressions, modifier logic, SME-built fixtures, rule precision reporting, feedback-driven threshold tuning |
| Patient identity errors (false merges) | Medium | High | Conservative auto-link threshold, identity review queue, audited unmerge |
| Poor scanned PDF quality | High | Medium | Template learning, human correction UI, arithmetic cross-check, LLM fallback |
| LLM too slow on older CPUs | Medium | Medium | Hardware tiers, async queue, AI Off fully supported |
| LLM hallucination in explanations or verdicts | Medium | High | Schema-constrained output, evidence-only prompts, post-checks, citation validation, advisory-only role |
| AMA CPT licensing | Medium | Medium | Ship codes without descriptors until licensed (D6) |
| No real labelled data early | High | Medium | Synthetic generator from day one; start real data collection in Phase 2 |
| SQLite used on a network share by several users | Medium | High | Detect network paths and block them; document single-user scope (D4) |
| Copyleft dependency slipping in | Low | High | CI license gate, SBOM |
| Reference data becomes stale | Medium | Medium | Dashboard warning when a dataset's effective period has passed |

## 14. Engine API contract (initial)

FastAPI generates the OpenAPI spec, and the UI client is generated from it (openapi-typescript). Initial endpoints:

```
GET  /health
POST /auth/login                      POST /auth/logout
POST /ingest/files                    GET  /ingest/jobs/{id}
POST /ingest/csv/mapping-templates    GET  /ingest/csv/mapping-templates
GET  /invoices?filters                GET  /invoices/{id}
GET  /flags?tier&rule&status&party&from&to&sort
GET  /flags/{id}                      POST /flags/{id}/review
POST /detect/sweep                    GET  /detect/sweep/{id}
GET  /parties/merge-suggestions       POST /parties/merge   POST /parties/unmerge
GET  /patients/link-suggestions       POST /patients/link   POST /patients/unlink
GET  /rules                           PATCH /rules/{id}     POST /rules/preview-impact
GET  /refdata/versions                POST /refdata/import
GET  /ai/status                       POST /ai/tier
POST /ai/explain/{flag_id}            POST /ai/triage/{flag_id}
POST /ai/ask                          GET  /ai/suggestions/{id}
GET  /reports/{name}?params           GET  /audit?filters   POST /audit/verify
POST /backup                          POST /restore
```

## 15. Glossary

- AP / AR: Accounts payable (bills we receive) and accounts receivable (bills we send).
- Blocking key: a set of fields used to find candidate pairs cheaply before detailed comparison.
- Fellegi-Sunter / Splink: a probabilistic record-linkage model that weighs field agreements. Splink is an open-source implementation.
- NCCI PTP: National Correct Coding Initiative procedure-to-procedure edits, meaning code pairs that should not normally be billed together.
- MUE / MAI: Medically Unlikely Edits (maximum units per code) and the MUE Adjudication Indicator (per claim line or per date of service).
- Global period: the 0, 10 or 90 days after a surgery during which related follow-up care is included in the surgical fee.
- Frequency code (CLM05-3): 1 = original claim, 7 = replacement, 8 = void.
- GGUF / Q4_K_M: the llama.cpp model file format and a 4-bit quantization level that balances size and quality.
- GBNF / JSON-schema constrained decoding: forces model output to match a grammar or schema exactly.
