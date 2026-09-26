# Security, privacy and HIPAA mapping

ABM Invoice Analytics handles PHI and is operated as HIPAA-covered. Everything runs on one
Windows PC with no network use. This document maps each control to its implementation and the
test that proves it.

## Controls

| Control | Implementation | Verified by |
|---|---|---|
| Encryption at rest | SQLCipher 4 (AES-256) with a random 256-bit key generated at install (`security/keystore.py`). On Windows the key set is protected with DPAPI (user scope, `CryptProtectData` with entropy). Dev on macOS uses the OS keyring; `file` mode exists for tests only. | `test_db_is_encrypted`: plain sqlite3 cannot open the file, no SQLite header |
| Field-level encryption | Patient first/last names are AES-256-GCM encrypted with a separate field key, with the column name as associated data (`security/crypto.py`). Backups and exports never contain plaintext names. | `test_field_cipher_roundtrip`, `test_patient_names_encrypted_at_field_level` |
| Pseudonymous matching | `patient_key`, tax IDs, MRN/member IDs and phonetic keys are HMAC-SHA256 with an install secret and a domain separator. Indexes never hold plaintext identifiers. Names are decrypted only in memory, for the length of an identity-resolution run. | code review; `test_identity_precision_on_hard_set` |
| Recovery key | Optional admin recovery key (200-bit, base32 groups) shown once at setup. It wraps the key set with scrypt + AES-GCM, and restores backups on a new machine. | `test_recovery_key`, `test_restore_on_new_install_with_recovery_key` |
| No network | The engine and llama-server bind to 127.0.0.1 only. The LLM client refuses non-loopback URLs and ignores proxy env vars. No telemetry or auto-update. All HTTP client code lives in one module. The Tauri CSP allows `connect-src` to `http://127.0.0.1:*` and IPC only. | `test_engine_process_opens_no_non_loopback_sockets` (real process, sampled sockets), `test_llm_client_refuses_non_loopback`, `test_source_has_no_outbound_http` |
| Local API security | Random port and 256-bit bearer token per launch, passed by the shell through environment variables. Constant-time token compare. Origin allow-list and a Host header check (DNS-rebinding defence). User sessions are a separate header. | `test_launch_token_origin_and_host_checks` |
| Sign-in mode | `security.require_login` (Settings → Sign-in). **Default off (demo / single-user desktop):** the app opens as the primary admin with no login screen and no idle lock; actions are still attributed to that user in the audit log (`LOGIN_AUTO`), and the per-launch token still protects the local API. **Turn it on before handling real PHI**, which enables the controls in the next row and the HIPAA mapping below. | `test_no_login_mode_auto_signs_in_primary_admin` |
| Access control (sign-in on) | Local users with argon2id hashes (min 10 chars, rehash on parameter change). Roles ADMIN ⊃ REVIEWER ⊃ VIEWER, enforced per endpoint. 15-minute idle lock enforced server-side (session expiry) and client-side (lock screen). | `test_users_sessions_and_idle_lock`, `test_viewer_cannot_act_as_reviewer`, `test_setup_once_login_and_roles` |
| Audit | Append-only `audit_log`, with UPDATE/DELETE blocked by triggers and hash-chained: `hash = SHA256(prev_hash ‖ canonical_json(row))`. It covers logins (incl. failures), PHI views (invoice detail, documents, drafts, named identity pairs), review decisions, merges/links, settings, refdata imports, exports, backups/restores, retention, and detection runs. The integrity check is in the Audit screen. | `test_audit_chain_and_tamper_detection` (drops the trigger, edits a row, verifier pinpoints it) |
| AI cannot decide | AI code gets its own DB connection with a SQLite authorizer that denies writes to every table except `ai_suggestions` and `jobs`. Outputs are schema-constrained and post-checked. | `test_ai_connection_cannot_write_flags_or_reviews`, `test_ai_module_sources_never_write_decision_tables` |
| NL→SQL safety | sqlglot validation (one SELECT; whitelisted views; allow-listed functions; LIMIT ≤ 500). Runs on a separate `mode=ro` + `query_only` connection with an authorizer that denies non-read actions and reads of sensitive columns (encrypted names, keyed hashes, password hashes, audit payloads), plus a 5 s progress-handler timeout. | `test_nlq_rejects_unsafe` (19 cases), `test_nlq_adversarial_fuzz_500` (0 non-SELECT executions, 0 access outside views, DB unchanged) |
| Logging | Structured JSON logs with IDs only. `RedactionFilter` scrubs dates, SSN/tax-ID/phone/email/ID-shaped tokens and `name=` style pairs, and flattens exception text. Logs rotate (10 MB × 5). | `test_no_phi_in_logs` (full ingest with unique PHI plus deliberately careless log lines) |
| SQLCipher memory hardening | `cipher_memory_security` is OFF (SQLCipher default) because it crashes the Windows sqlcipher3 wheel with a stack overflow on the first write. Keys are held only in the engine process. | Windows CI diagnostic |
| Temp files | None containing PHI. DuckDB is in-memory with spilling disabled and external access off. OCR pipes PNG bytes via stdin/stdout. Uploads stay in memory. PDF originals are stored inside the encrypted DB. | code review |
| Malicious input | PDF/image extraction runs in a spawned subprocess with a 120 s timeout. Parsers never raise into ingest: malformed files become parse issues. Uploaded filenames are reduced to their base name. Bundle zip paths are checked for traversal. | `test_fuzz_x12_never_crashes`, `test_fuzz_csv_never_crashes` (found and fixed a real crash), `test_fuzz_pdf_never_crashes`, `test_upload_filename_cannot_traverse` |
| Reference-data integrity | `.vref` bundles are Ed25519-signed over the manifest, which pins each file's SHA-256. Only public keys embedded in the app are trusted. Import is one transaction. | `test_refdata_import_requires_trusted_signature`, `test_tampered_bundle_rejected` |
| Model integrity | Models load only if listed in `models/manifest.json` with a matching SHA-256 and an Apache-2.0/MIT licence. Package import streams and verifies each file. | `ai/runtime.py:verify_model`, `ai/models.py` |
| Backup / restore | Encrypted `.vbak`. Restore verifies integrity, swaps the file after quiescing every DB user, and keeps the previous DB as `.pre-restore`. Schema upgrades copy the encrypted DB to `backups/pre-migrate-<ts>.db` first. | `test_restore_roundtrip` (backup → mutate → restore → reopen → data, flags and audit chain intact), `test_restore_on_new_install_with_recovery_key`, `test_pre_migration_backup` (run on the Windows CI runner) |
| Supply chain | Pinned lockfiles (`uv.lock`, `package-lock.json`), runtime licence gates in CI, CycloneDX SBOMs, signed binaries at release. | CI workflow |
| Sidecar lifecycle | The shell kills the engine on exit. The engine also watches the shell's PID and exits if it disappears (crash or force-quit), so the DB is never left open by an orphan. | manual test (kill -9 of the shell → engine exits within 2 s) |
| Network shares | The DB refuses to open on SMB/NFS/UNC paths. | `db/connection.py:is_network_path` |

## HIPAA Security Rule mapping (45 CFR §164.312)

This mapping assumes **Require users to sign in** is on. With the default demo mode (off), unique
user identification, automatic logoff and person authentication are not enforced.

| Standard | How it is met |
|---|---|
| (a)(1) Access control: unique user identification | Named local accounts, no shared logins, role-based permissions |
| (a)(2)(iii) Automatic logoff | 15-minute idle lock (server session expiry + client lock screen) |
| (a)(2)(iv) Encryption and decryption | SQLCipher AES-256 at rest; field-level AES-GCM for names; encrypted backups |
| (b) Audit controls | Hash-chained append-only audit log incl. PHI views; integrity verification |
| (c)(1) Integrity | Hash chain detects alteration; signed reference data; SQLCipher HMAC page authentication |
| (d) Person or entity authentication | argon2id password authentication per user |
| (e)(1) Transmission security | Not applicable: no PHI is transmitted. All traffic is on the loopback interface inside one machine. |

## Residual risks and operator responsibilities

- A local administrator of the Windows account can read DPAPI-protected keys. Protect the
  Windows account (BitLocker, strong sign-in, screen lock).
- Exports (CSV/PDF reports, flag exports) contain PHI once saved. Handle them per policy. Every
  export is audited.
- The recovery key is equivalent to the database key. Print it, store it offline, and never keep
  a digital copy next to backups.
- The optional Windows Firewall block rule is recommended as defence in depth (see admin guide).
