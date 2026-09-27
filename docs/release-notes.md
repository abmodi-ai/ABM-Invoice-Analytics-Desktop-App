## Download

| Your computer | File |
|---|---|
| Windows 10 / 11 (64-bit) | `ABM-Invoice-Analytics-Setup-x64.exe` |
| Mac with Apple Silicon, macOS 14 Sonoma (fully updated) or newer | `ABM-Invoice-Analytics-macOS-AppleSilicon.dmg` |
| Linux 64-bit | `ABM-Invoice-Analytics-Linux-x86_64.AppImage` |
| All | `SHA256SUMS.txt` — checksums to verify the download |

Each file is the whole app. Everything runs on your computer; the app makes no internet
connections.

## First run

- **Windows:** the installer is not code-signed yet, so SmartScreen shows "Windows protected your
  PC". Click **More info → Run anyway**. It installs for your account, no administrator rights.
- **Mac:** drag the app to Applications. It is not notarized yet: the first time, macOS says it
  can't verify it. Click **Done**, open **System Settings → Privacy & Security**, click **Open
  Anyway** and confirm.
- **Linux:** `chmod +x` the AppImage and run it. Ubuntu 24.04+ needs `sudo apt install libfuse2t64`
  once.

The app opens straight to the dashboard with an empty database. Drop invoices (PDF, CSV/Excel,
X12 837/835) on the **Ingest** page and review flags in the **Review** queue. Before using it with
real patient data, turn on **Settings → Sign-in → Require users to sign in**.

Full instructions: [README](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App#download).
