# ABM Invoice Analytics

An offline Windows desktop app that finds duplicate billing: duplicate invoices (AP and AR),
duplicate or over-billed clinical charges for the same patient, and problems on clinical-trial
site invoices (the same charge twice, repeated high-cost procedures, two protocol visits on one
date). Every flag shows exactly why it was raised. It proposes; people decide. Nothing leaves the
machine.

## Download and install (Windows)

**[⬇ Download ABM-Invoice-Analytics-Setup-x64.exe](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest/download/ABM-Invoice-Analytics-Setup-x64.exe)**
(about 105 MB), or pick a version on the [Releases page](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest).

1. Download `ABM-Invoice-Analytics-Setup-x64.exe` and double-click it.
2. The installer isn't code-signed yet, so Windows shows **"Windows protected your PC"**.
   Click **More info**, then **Run anyway**.
3. Setup installs for your Windows account in under a minute. No administrator rights are
   needed.
4. Open **ABM Invoice Analytics** from the Start menu. It opens straight to the dashboard.
5. Go to **Ingest** and drop in invoices: PDF (digital or scanned), CSV/Excel, or X12 837/835
   files. Flags appear in the **Review** queue.

| | |
|---|---|
| Requirements | Windows 10 or 11, 64-bit. About 1 GB free disk space. No internet connection needed. |
| For IT deployment | `ABM-Invoice-Analytics-x64.msi` on the same release page installs for all users. See the [admin guide](docs/admin-guide.md). |
| Verify the download | Compare against `SHA256SUMS.txt` on the release page: `Get-FileHash .\ABM-Invoice-Analytics-Setup-x64.exe` in PowerShell. |
| Your data | Stored encrypted in `%LOCALAPPDATA%\InvoiceAnalytics`. Uninstalling the app (Settings → Apps) keeps it; delete that folder to remove it. |
| Patient data | Sign-in is off by default for demos. Turn on **Settings → Sign-in → Require users to sign in** before using real patient data. |

How to use the app: [user guide](docs/user-guide.md).

## For developers

- **Engine**: Python 3.12, FastAPI, SQLCipher (system of record), in-memory DuckDB (rule passes)
- **Desktop**: Tauri 2 shell + React/TypeScript UI; the engine runs as a supervised loopback sidecar
- **AI (optional)**: llama.cpp `llama-server`, GGUF models, schema-constrained, advisory only

```bash
make setup && make demo && make dev     # run the desktop app on synthetic data
make test                               # engine tests + UI unit tests
```

Rules: INV-001…008 (invoice level), CLN-001…013 (clinical and trial-site), SUP-001…006
(suppressions). Every flag carries a machine- and human-readable evidence trail, the
rule/engine/refdata versions that produced it, and an audited review decision.

**Releasing.** Bump the version in `apps/desktop/src-tauri/tauri.conf.json` (and the other
manifests), then push a matching tag, e.g. `git tag -a v0.2.1 -m v0.2.1 && git push origin v0.2.1`.
CI runs the full test suite, builds the installers, and test-installs the setup.exe on a clean
Windows runner. Only then does it publish the GitHub Release.

| | |
|---|---|
| Security & HIPAA mapping | [docs/security.md](docs/security.md) |
| Guides | [user](docs/user-guide.md) · [admin](docs/admin-guide.md) · [reference-data runbook](docs/runbook-refdata.md) · [developer](docs/dev.md) |
