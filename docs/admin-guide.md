# Administrator guide

## Installation

1. Install the app for each user: Windows `ABM-Invoice-Analytics-Setup-x64.exe` (silent install:
   `ABM-Invoice-Analytics-Setup-x64.exe /S`), Mac `.dmg`, or Linux `.AppImage`. See the README.
2. Start the app. On first run, create the administrator account and **print the recovery key**.
   It is shown once. It is the only way to restore a backup on a different computer.
3. Import the current reference-data bundle (Reference data → Import signed bundle).
4. Optionally add a Windows Firewall rule that blocks all network access for the app
   (defence in depth; the app makes no network connections):
   `New-NetFirewallRule -DisplayName "ABM Invoice Analytics block" -Program "$env:LOCALAPPDATA\ABM Invoice Analytics\engine\invoice-analytics-engine.exe" -Direction Outbound -Action Block`

Data locations:

| What | Where |
|---|---|
| Encrypted database, key blob, logs | `%LOCALAPPDATA%\InvoiceAnalytics\` |
| Shared reference data and AI models | `%PROGRAMDATA%\InvoiceAnalytics\` |
| Backups | configurable (Settings → Backup) |

The database refuses to run from a network share. ABM Invoice Analytics is single-user per install.

## Users and roles

| Role | Can |
|---|---|
| Viewer | View dashboards, flags, invoices, reports |
| Reviewer | + ingest, decide flags, correct extractions, link patients, run sweeps |
| Admin | + users, rules, reference data, AI, settings, backup/restore, retention, audit log, merge parties |

Passwords need at least 10 characters (argon2id hashed). Deactivate users rather than deleting
them, so the audit trail keeps its names.

## Rules

Rules → **Edit** changes thresholds or tiers. Always use **Preview impact** first: it reports how
many flags the change would add, remove or re-tier, without applying anything. Saved changes are
versioned and audited; run **Full sweep** to re-evaluate existing records. The Threshold tuning
report (Reports) suggests changes from real review decisions once a rule has 20 or more reviews.

Frequency limits (CLN-007) and legitimate recurring series (SUP-004, for example dialysis 3× a
week) are maintained under Reference data.

## Identity (Parties & patients)

- Vendors that share an exact tax ID or NPI are merged automatically; the merge is audited.
- Other similar vendors are **suggested** only. Merge or keep separate, and unmerge at any time.
- Patients auto-link only when the evidence is strong and first name, last name and date of birth
  all agree at some level. Everything else goes to **Patient link review**. Names are hidden by
  default; showing them is logged as a PHI view.

## Local AI (optional)

Settings → Local AI shows the machine's RAM, cores and AVX2 support, and the tiers it can run.

1. Import the model package: Settings → **Import model package…** → `ia-models-lite.zip`.
   Hashes and licences are verified before anything is installed.
2. Choose the tier and **Apply**.

The model starts on the first AI job and stops after 10 idle minutes. Everything works with AI off.
Background AI work per detection run is capped (`ai.max_background_jobs_per_run`, default 200,
largest amounts first). Reviewers can still request **Investigate** on any flag.

## Backup and restore

Settings → Backup and restore. Backups are encrypted `.vbak` files. Enter the recovery key when
creating a backup to make it restorable on another computer. Restore replaces the database. The
previous one is kept as `invoice-analytics.pre-restore`, and the engine restarts automatically.
Test a restore regularly.

## Retention

Settings → Data retention soft-deletes invoices older than the retention period (default 7 years).
Optionally it also purges the encrypted names of patients no longer referenced. Preview first;
every run is audited.

## Audit log

Admin → Audit log. **Check integrity** re-computes the hash chain and pinpoints any altered or
removed entry.

## Uninstall

The uninstaller keeps your data unless you choose **Remove all data**, which overwrites and deletes
the database and removes the keys (`invoice-analytics-engine --remove-all-data`).
