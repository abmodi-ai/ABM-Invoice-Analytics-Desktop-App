# Runbook: quarterly reference-data update

CMS publishes NCCI PTP and MUE files quarterly (January, April, July, October) and the Physician
Fee Schedule RVU file (global days) with each PFS release. HCPCS Level II is updated quarterly.
Build bundles on the **build machine** (internet access, holds the offline signing key). Client
installs never download anything.

## One-time setup

```bash
uv run python refdata/make_signing_key.py refdata/keys/release-signing
```

Put the printed public key into `TRUSTED_PUBLIC_KEYS` in
`engine/invoice_analytics/clinical/refdata.py` (remove the development key) and ship that build. Keep
`release-signing.private` offline (encrypted USB or HSM). It must never be committed.

## Each quarter

1. From the CMS NCCI, MUE and PFS pages, copy the file URLs into `sources-<Q>.json`:
   ```json
   {"ptp_practitioner": "https://www.cms.gov/files/zip/...", "ptp_hospital": "...",
    "mue_practitioner": "...", "mue_hospital": "...", "pfs_rvu": "...", "hcpcs": "..."}
   ```
2. Download (only `https://www.cms.gov/` URLs are accepted; SHA-256s are recorded):
   ```bash
   uv run python refdata/fetch_cms.py sources-2026Q4.json --out refdata/downloads/2026Q4
   ```
3. Unzip the PFS RVU archive and pick the `PPRRVU*.csv`. Unzip the HCPCS archive and pick the
   fixed-width `.txt`.
4. Build and sign:
   ```bash
   uv run python refdata/build_bundle.py \
     --ptp-practitioner refdata/downloads/2026Q4/ptp_practitioner.zip \
     --ptp-hospital refdata/downloads/2026Q4/ptp_hospital.zip \
     --mue-practitioner <mue practitioner csv/xlsx> --mue-hospital <mue hospital csv/xlsx> \
     --pfs-rvu <PPRRVU csv> --hcpcs <HCPCS txt> \
     --frequency-limits client/frequency_limits.csv --recurring client/recurring_series.csv \
     --version 2026Q4 --effective-from 2026-10-01 --effective-to 2026-12-31 \
     --key /secure/release-signing.private --out refdata/out/ia-refdata-2026Q4.vref
   ```
   Row counts are printed per dataset. A dataset with no recognised rows fails the build (CMS
   layout change). Update the header detection in `build_bundle.py` and its test.
5. Sanity check on a test install: import the bundle, run **Full sweep**, and compare flag counts
   for CLN-004/005/006 with the previous quarter. Large swings need review with the coding SME.
6. Distribute the `.vref` (download or USB). Admins import it under Reference data. Behaviour
   changes without a code release; each flag records the refdata version it used.

Rules look up reference rows by each line's **date of service**, so re-running old claims uses the
edits that applied then. The Dashboard warns when an installed dataset's effective period has
passed.

CPT descriptors are AMA-licensed and are not included. Codes work without them.
