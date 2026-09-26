## Download

| File | What it is |
|---|---|
| `ABM-Invoice-Analytics-Setup-x64.exe` | **Most people want this.** Installs for the current user; no administrator rights needed. |
| `ABM-Invoice-Analytics-x64.msi` | For IT departments deploying to many PCs. Installs for all users. |
| `SHA256SUMS.txt` | Checksums to verify the download. |

Requires Windows 10 or 11 (64-bit). About 1 GB of free disk space. Everything runs on your
computer; the app makes no internet connections.

## First run

The installer is not yet code-signed, so Windows SmartScreen shows **"Windows protected your
PC"**. Click **More info → Run anyway**. This warning goes away once the installer is signed.

The app opens straight to the dashboard with an empty database. Drop invoices (PDF, CSV/Excel,
X12 837/835) on the **Ingest** page and review flags in the **Review** queue. Before using it with
real patient data, turn on **Settings → Sign-in → Require users to sign in**.

Uninstall from **Settings → Apps**. Your data stays in `%LOCALAPPDATA%\InvoiceAnalytics` unless you delete that folder.
