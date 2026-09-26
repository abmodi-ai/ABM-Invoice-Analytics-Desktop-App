# ADR 0002: License policy scope

Status: accepted

Policy (spec 3.1): no GPL/AGPL runtime dependencies. `tools/license_check.py` checks the
transitive closure of the engine's runtime dependencies only; the npm gate uses
`license-checker --production`. Build and dev tools never ship and are out of scope:

- PyInstaller: GPL with the bootloader exception (its output may ship with commercial apps).
- hypothesis: MPL-2.0, test-only.
- pytest, mypy, ruff, black, pip-licenses, cyclonedx-bom: dev-only.

Reviewed runtime exceptions (file-level weak copyleft, shipped unmodified): certifi (MPL-2.0 CA
bundle), tqdm (MPL-2.0 AND MIT). pypdfium2 lists several permissive licences in one string.

The gate has already caught one violation: Splink 4 pulls in igraph (GPL). See ADR 0005.
The Double Metaphone implementation is the BSD-licensed `metaphone` package; the similarly named
`doublemetaphone` package (Artistic licence) was rejected. OCR calls the Tesseract binary
(Apache-2.0) directly instead of through pytesseract, which writes images to temp files (ADR 0006).
