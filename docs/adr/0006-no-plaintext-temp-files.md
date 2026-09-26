# ADR 0006: No plaintext PHI on disk outside the encrypted database

Status: accepted

- DuckDB is in-memory only, with `temp_directory=''`, `max_temp_directory_size=0B` and
  `enable_external_access=false`. It is capped at 1.5 GB (`memory_limit`) and fails rather than
  spill to disk. Rules are written to stay within this cap at 1.25M lines (ADR 0007).
- OCR: page images are rendered in memory (pypdfium2) and piped to `tesseract stdin stdout tsv`.
  pytesseract is not used because it writes images and output to temp files.
- PDF/image originals, needed by the correction view, are stored as BLOBs inside the SQLCipher DB
  (`document_blobs`), not as loose files.
- Uploaded files stay in memory in the ingest job queue until processed (at most an hour when
  waiting for a mapping), and are never written to a staging folder.
- Embeddings are read on demand from the DB for candidate pairs only.
- Backups are `sqlcipher_export`s encrypted with a fresh random key that is wrapped by the install
  key and, optionally, by the admin recovery key.
