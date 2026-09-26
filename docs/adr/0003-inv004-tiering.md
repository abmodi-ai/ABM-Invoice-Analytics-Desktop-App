# ADR 0003: INV-004 guards against sequential invoice numbers

Status: accepted (deviation from spec 5.4 INV-004)

Spec: same party + OCR-folded invoice numbers with Damerau-Levenshtein ≤ 1 or Jaro-Winkler ≥ 0.92
+ total within ±$0.05 → PROBABLE.

The golden set showed this rule, as written, produced most PROBABLE false positives. A vendor's
recurring fixed-amount invoices (monthly contracts, weekly therapy blocks) carry consecutive
numbers that are one character apart, and JW ≥ 0.92 holds for almost any two long numbers that
share a prefix. Changes, all admin-configurable under `rules.INV-004`:

- `min_length` (4): shorter numbers are one edit apart by chance.
- `sequential_gap` (3): purely numeric numbers within 3 of each other are skipped.
- `max_damerau_with_jaro_winkler` (2): the JW criterion also needs DL ≤ 2.
- `probable_max_date_gap_days` (3): a genuine OCR re-key or re-sent scan folds to the *same*
  number or keeps (nearly) the same invoice date. A single differing character on invoices more
  than 3 days apart is reported one tier lower (WEAK).
- Invoice-level amount/number rules (INV-003/004/005) skip pairs whose invoices bill disjoint
  sets of patients; two invoices for different patients are not the same bill.

Effect on the synthetic golden set: PROBABLE precision 0.48 → 1.00; OCR-typo recall unchanged
at 1.00 (OCR prefix corruption like "1NV" for "INV" is now normalised, see normalize/identifiers).
