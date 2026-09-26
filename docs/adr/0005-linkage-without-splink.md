# ADR 0005: Patient linkage without Splink

Status: accepted (deviation from spec 3.1 / 5.3, which name Splink)

Splink 4 depends on `igraph`, which is GPL. That violates the no-GPL-runtime rule, and the
license gate caught it. Rather than vendor a Splink build with the dependency stripped, the engine
implements the same model directly (`linkage/fellegi_sunter.py`, about 200 lines, numpy only):

- Comparisons with discrete levels: first name (exact / nickname table / JW ≥ 0.9 / else), last
  name (exact / JW ≥ 0.92 / Double Metaphone / else), DOB (exact / day-month transposed / year off
  by one / else), sex, ZIP, shared MRN/member ID (keyed hashes).
- Blocking on DOB, (phonetic surname, birth year), (surname, canonical first name), transposed DOB
  and source IDs.
- u from 100k random pairs; m and the prior λ by EM over blocked pairs.
- The P(else | match) level is capped at 5% for names, DOB and sex. Without the cap, EM "found" a
  cluster of look-alikes (same surname + DOB + ZIP, different first name) and learned them as matches.
- Conservative auto-link policy: complete disagreement on first name, last name or DOB never
  auto-links (twins and relatives break the independence assumption). Such pairs go to the review
  queue whatever their score.
- Every suggestion stores its per-field levels and weights for the reviewer.

Result on the hard identity test (typo, nickname and transposed-DOB duplicates with no shared ID,
plus look-alike relatives): auto-link precision 1.0 (target ≥ 0.995). The remaining true
duplicates and all look-alikes go to review. Pandas is no longer a runtime dependency.
