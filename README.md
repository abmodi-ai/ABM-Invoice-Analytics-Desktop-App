# Verismo Invoice Tracker

An offline Windows desktop app that manages invoices and finds duplicate billing: duplicate
invoices (AP and AR), and duplicate or over-billed clinical charges for the same patient or
treatment. Detection is deterministic (normalization, blocking, fuzzy matching, probabilistic
record linkage and CMS edit tables). An optional local LLM explains, triages, reads messy
documents and answers questions. It proposes; people decide. Nothing leaves the machine.

- **Engine**: Python 3.12, FastAPI, SQLCipher (system of record), in-memory DuckDB (rule passes)
- **Desktop**: Tauri 2 shell + React/TypeScript UI; the engine runs as a supervised loopback sidecar
- **AI (optional)**: llama.cpp `llama-server`, GGUF models, schema-constrained, advisory only

| | |
|---|---|
| Spec | [docs/implementation-plan.md](docs/implementation-plan.md) |
| What is built, what is left | [docs/status.md](docs/status.md) |
| Security & HIPAA mapping | [docs/security.md](docs/security.md) |
| Decisions | [docs/adr/](docs/adr/) |
| Guides | [user](docs/user-guide.md) · [admin](docs/admin-guide.md) · [reference-data runbook](docs/runbook-refdata.md) · [developer](docs/dev.md) |

```bash
make setup && make demo && make dev     # run the desktop app on synthetic data
make test                               # 301 engine tests + UI unit tests
```

Rules: INV-001…008 (invoice level), CLN-001…009 (clinical), SUP-001…006 (suppressions). Every
flag carries a machine- and human-readable evidence trail, the rule/engine/refdata versions that
produced it, and an audited review decision.
