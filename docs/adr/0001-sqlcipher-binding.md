# ADR 0001: SQLCipher binding

Status: accepted

The system of record is SQLite encrypted with SQLCipher 4 through the `sqlcipher3` Python
package (0.6.2, bundled SQLCipher 4.12 community). It was chosen after a smoke test before any
code was written: a keyed database opens with `sqlcipher3`, and plain `sqlite3` reports "file is
not a database". Prebuilt wheels exist for `win_amd64`, `macosx_11_0_arm64` and Linux, so no
native toolchain is needed on the Windows CI runner.

The 256-bit DB key is a raw hex key (`PRAGMA key = "x'…'"`), so there is no KDF cost on open.
`cipher_memory_security` is on and `temp_store = MEMORY`, so SQLite never writes plaintext temp
files. WAL mode is used; WAL pages are encrypted like the main file.
