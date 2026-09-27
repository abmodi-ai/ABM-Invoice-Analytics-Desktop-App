# ABM Invoice Analytics: user guide

ABM Invoice Analytics finds bills that were charged twice: the same invoice sent again, a claim re-billed under
a new number, the same service billed twice for one patient, units over Medicare limits, bundled
procedures billed separately, and similar problems. It works entirely on your computer. Nothing is
sent anywhere.

ABM Invoice Analytics **flags**; you **decide**. Every flag shows exactly why it was raised.

## Installing

Download the file for your computer from the
[latest release](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App/releases/latest): `ABM-Invoice-Analytics-Setup-x64.exe` (Windows),
`ABM-Invoice-Analytics-macOS-AppleSilicon.dmg` (Mac) or `ABM-Invoice-Analytics-Linux-x86_64.AppImage`
(Linux). Step-by-step instructions, including the one-time security prompts while the downloads
are not yet signed by Microsoft and Apple, are in the [README](https://github.com/abmodi-ai/ABM-Invoice-Analytics-Desktop-App#install).
The app needs no internet connection.

## Signing in

By default the app opens straight to the dashboard; there is no login screen. An administrator can
switch on **Settings → Sign-in → Require users to sign in**. It must be on before the app is used
with real patient data. With it on, you sign in with your own account, and the app locks after 15
minutes without activity. Use **Lock** in the sidebar when you step away.

## Tiers

| Tier | Meaning | What to do |
|---|---|---|
| Hard | Almost certainly a duplicate | Stop payment or billing, then confirm |
| Probable | Likely a duplicate | Review |
| Weak | Worth a look | Shown only when you filter for it |
| Info | Context | No action needed |

Tiers are shown with an icon and a label, never by colour alone.

## Bringing in invoices (Ingest)

Drag files onto the Ingest page, or click **Choose files**. Supported formats:

- **CSV / Excel exports.** The first time ABM Invoice Analytics sees a file layout, it asks you to **map
  columns** (for example, "Inv #" is the invoice number). Suggestions are pre-filled. Save the
  mapping as a template and files with the same header row import automatically next time.
- **X12 claims (837P, 837I)** and **remittances (835)**. 835 files mark matching claims as paid.
- **PDF invoices**, digital or scanned. If ABM Invoice Analytics is not confident it read a document correctly,
  it puts it under **Documents waiting for correction**: the PDF on the left, the fields on the
  right, with doubtful fields highlighted. Fix them and click **Accept and ingest**. ABM Invoice Analytics
  remembers the vendor's layout and reads the next one better.

Dates that could be read two ways (03/04/2026) are never guessed. They are marked **check date**.
Choose the date order in the mapping wizard.

After each file, the **Billed before?** column answers whether anything on it was already billed on
another invoice: **Yes**, with links to the matching flags, or **No**, with how many earlier lines for
the same patients or trial subjects it was checked against. A trial subject billed again for a
protocol visit (for example Month 1 Day 1) that is already on another invoice is flagged (CLN-013),
even when the date or amount differs.

Files in a **watched folder** (set by your administrator) are picked up automatically.

## Reviewing flags (Review queue)

The queue is sorted by tier, then flags that match **another invoice** (billed before), then
confidence and amount. Each flag is labelled **vs. another invoice** or **same invoice**; use
**Compared with** to show only one kind. For each flag you see:

- **Summary**: one sentence saying what matched.
- **Side by side**: this record and the earlier record. Fields that **match** are green; fields
  that **differ** are amber. The line that triggered the flag is outlined.
- **Evidence**: every field compared, the method used (exact, Damerau distance, embedding
  similarity…), any reference data used (MUE, NCCI, global days) and the suppressions checked
  (credit memo, corrected claim, recurring series…).
- **AI assistance** (if switched on): a plain-language explanation and a triage verdict with its
  full trace. These are **advice only**. An explanation that fails its fact check is discarded,
  never shown.

Keyboard: **J / K** next and previous · **C** confirm duplicate · **D** dismiss · **N** needs info ·
**Enter** submit · **Esc** cancel. Pick a reason code (required reporting data). When confirming,
you can record the **amount recovered**; it feeds the dashboard and reports.

Dismissing a pair as *not a duplicate* teaches ABM Invoice Analytics: the same pair is not raised again (it is
kept for audit).

## Invoices and search

**Invoices** lists everything, with filters for vendor, number, status, dates and flagged state.
Open an invoice to see its lines, patients, source document, flags and history.

**Search / Ask** runs read-only queries on curated views. With AI switched on you can ask in plain
language ("which vendors had the most confirmed duplicates this quarter?"). ABM Invoice Analytics shows the SQL
it generated before running it, and it can only ever read, never change, data.

## Reports

Duplicates found and recovered by period and vendor, review throughput, rule precision, and
threshold-tuning suggestions. Export to CSV or PDF. Exports contain patient information, so handle
them according to your organisation's policy. Every export is logged.
