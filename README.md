# ABM Invoice Analytics

An offline desktop app for Windows, Mac and Linux that finds duplicate billing: duplicate invoices
(AP and AR), duplicate or over-billed clinical charges for the same patient, and problems on
clinical-trial site invoices (the same charge twice, a visit billed again on another invoice, two
protocol visits on one date). Every flag shows exactly why it was raised. It proposes; people
decide. Nothing leaves the machine.

## Download

| Your computer | Download | Size |
|---|---|---|
| **Windows** 10 / 11 (64-bit) | **[⬇ ABM-Invoice-Analytics-Setup-x64.exe](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest/download/ABM-Invoice-Analytics-Setup-x64.exe)** | ~105 MB |
| **Mac** with Apple Silicon (M1 or newer), macOS 14+ | **[⬇ ABM-Invoice-Analytics-macOS-AppleSilicon.dmg](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest/download/ABM-Invoice-Analytics-macOS-AppleSilicon.dmg)** | ~150 MB |
| **Linux** 64-bit (Ubuntu 22.04+, Debian 12+, Fedora 38+) | **[⬇ ABM-Invoice-Analytics-Linux-x86_64.AppImage](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest/download/ABM-Invoice-Analytics-Linux-x86_64.AppImage)** | ~200 MB |

All versions and checksums: [Releases page](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest). Each download is the whole app; no
internet connection is needed after it is installed.

### Install

**Windows**
1. Run `ABM-Invoice-Analytics-Setup-x64.exe`.
2. The installer isn't code-signed yet, so Windows shows **"Windows protected your PC"**: click
   **More info → Run anyway**.
3. It installs for your account in under a minute (no administrator rights). Open **ABM Invoice
   Analytics** from the Start menu.

**Mac**
1. Open `ABM-Invoice-Analytics-macOS-AppleSilicon.dmg` and drag **ABM Invoice Analytics** onto
   **Applications**.
2. The app isn't notarized by Apple yet, so the first time you open it macOS says it can't verify
   the app. Click **Done**, then open **System Settings → Privacy & Security**, scroll down, click
   **Open Anyway**, and confirm. After that it opens normally.

**Linux**
1. Make the download executable and run it:
   `chmod +x ABM-Invoice-Analytics-Linux-x86_64.AppImage && ./ABM-Invoice-Analytics-Linux-x86_64.AppImage`
   (or right-click it → Properties → *Allow executing as program*, then double-click).
2. Ubuntu 24.04 and newer need the AppImage helper once: `sudo apt install libfuse2t64`.

Then go to **Ingest** and drop in invoices: PDF (digital or scanned), CSV/Excel, or X12 837/835
files. Flags appear in the **Review** queue.

| | |
|---|---|
| Your data | Stored encrypted on your computer: Windows `%LOCALAPPDATA%\InvoiceAnalytics`, Mac `~/Library/Application Support/InvoiceAnalytics`, Linux `~/.local/share/invoice-analytics`. Removing the app keeps it; delete that folder to remove it. |
| Encryption key | Held by Windows (DPAPI), the macOS Keychain, or the Linux keyring (GNOME Keyring/KWallet). On Linux without a keyring it is kept in a file only your account can read; use full-disk encryption there. |
| Verify a download | Compare with `SHA256SUMS.txt` on the release page (`Get-FileHash` on Windows, `shasum -a 256` on Mac, `sha256sum` on Linux). |
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
CI runs the full test suite and builds each download, then test-installs it on a clean
Windows, macOS and Linux machine. Only when all three pass does it publish the GitHub Release.

| | |
|---|---|
| Security & HIPAA mapping | [docs/security.md](docs/security.md) |
| Guides | [user](docs/user-guide.md) · [admin](docs/admin-guide.md) · [reference-data runbook](docs/runbook-refdata.md) · [developer](docs/dev.md) |
