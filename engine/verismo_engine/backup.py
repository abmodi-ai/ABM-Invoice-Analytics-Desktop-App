"""Encrypted backup and restore (.vbak), retention, and secure data removal.

A .vbak is a zip with:
  db.sqlcipher  - sqlcipher_export of the live DB, encrypted with a fresh random backup key
  meta.json     - schema version, created_at, and the backup key + install keyset wrapped
                  (a) with the install DB key (same-machine restore) and
                  (b) with the admin recovery key when one is configured (new-machine restore)
"""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlcipher3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from verismo_engine.context import Engine
from verismo_engine.db.migrate import current_version
from verismo_engine.security import audit
from verismo_engine.security.crypto import KeySet, unwrap_with_passphrase, wrap_with_passphrase
from verismo_engine.security.keystore import Keystore


class BackupError(RuntimeError):
    pass


def _wrap(key: bytes, secret: bytes) -> str:
    n = secrets.token_bytes(12)
    return (n + AESGCM(key).encrypt(n, secret, b"verismo-backup")).hex()


def _unwrap(key: bytes, blob_hex: str) -> bytes:
    b = bytes.fromhex(blob_hex)
    return AESGCM(key).decrypt(b[:12], b[12:], b"verismo-backup")


def create_backup(eng: Engine, dest_dir: Path, *, user_id: int | None, recovery_key: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tmp_db = dest_dir / f".verismo-{ts}.tmp"
    bkey = secrets.token_bytes(32)
    with eng.db.write_lock:
        c = eng.db.conn
        c.execute(f"ATTACH DATABASE ? AS bak KEY \"x'{bkey.hex()}'\"", (str(tmp_db),))
        try:
            c.execute("SELECT sqlcipher_export('bak')")
        finally:
            c.execute("DETACH DATABASE bak")
    payload = eng.keys.to_bytes()
    meta: dict[str, Any] = {
        "format": "vbak/1",
        "created_at": ts,
        "schema_version": current_version(eng.db),
        "engine_version": eng.engine_version,
        "backup_key_by_install": _wrap(eng.keys.db_key, bkey),
        "keyset_by_backup_key": _wrap(bkey, payload),
    }
    if recovery_key:
        meta["backup_key_by_recovery"] = wrap_with_passphrase(bkey, recovery_key).decode()
    out = dest_dir / f"verismo-backup-{ts}.vbak"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.write(tmp_db, "db.sqlcipher")
        z.writestr("meta.json", json.dumps(meta, indent=1))
    tmp_db.unlink()
    audit.record(
        eng.db,
        "BACKUP_CREATE",
        user_id=user_id,
        entity_type="backup",
        entity_id=out.name,
        after={"schema_version": meta["schema_version"], "with_recovery": bool(recovery_key)},
    )
    return out


def inspect_backup(
    path: Path, *, db_key: bytes | None = None, recovery_key: str | None = None
) -> tuple[dict[str, Any], bytes, KeySet]:
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("meta.json"))
    bkey = None
    if db_key is not None:
        try:
            bkey = _unwrap(db_key, meta["backup_key_by_install"])
        except Exception:  # noqa: BLE001 - different install
            bkey = None
    if bkey is None and recovery_key and meta.get("backup_key_by_recovery"):
        bkey = unwrap_with_passphrase(meta["backup_key_by_recovery"].encode(), recovery_key)
    if bkey is None:
        raise BackupError("cannot decrypt backup: wrong install or recovery key")
    keyset = KeySet.from_bytes(_unwrap(bkey, meta["keyset_by_backup_key"]))
    return meta, bkey, keyset


def restore_backup(eng: Engine, path: Path, *, user_id: int | None, recovery_key: str | None = None) -> dict[str, Any]:
    """Restore into the live data dir. The current DB is kept as <db>.pre-restore until success."""
    meta, bkey, keyset = inspect_backup(path, db_key=eng.keys.db_key, recovery_key=recovery_key)
    work = eng.config.data_dir / ".restore"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    with zipfile.ZipFile(path) as z:
        z.extract("db.sqlcipher", work)
    src = work / "db.sqlcipher"
    check = sqlcipher3.connect(str(src))
    check.execute(f"PRAGMA key = \"x'{bkey.hex()}'\"")
    ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        check.close()
        raise BackupError("backup failed integrity check")
    staged = work / "restored.db"
    check.execute(f"ATTACH DATABASE ? AS live KEY \"x'{keyset.db_key.hex()}'\"", (str(staged),))
    check.execute("SELECT sqlcipher_export('live')")
    check.execute("DETACH DATABASE live")
    check.close()
    db_path = eng.config.db_path
    eng.close()
    for suffix in ("-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    pre = db_path.with_suffix(".pre-restore")
    if db_path.exists():
        os.replace(db_path, pre)
    os.replace(staged, db_path)
    if keyset != eng.keys:
        Keystore(eng.config.data_dir, eng.config.keystore).save(keyset)
    shutil.rmtree(work, ignore_errors=True)
    return {
        "restored_from": path.name,
        "schema_version": meta["schema_version"],
        "created_at": meta["created_at"],
        "keys_replaced": keyset != eng.keys,
        "previous_db": pre.name,
    }


# ---------------------------------------------------------------- retention
def retention_preview(eng: Engine, years: int) -> dict[str, Any]:
    cutoff = (datetime.now(UTC).date() - timedelta(days=365 * years)).isoformat()
    return {
        "cutoff": cutoff,
        "invoices": eng.db.scalar(
            "SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL AND invoice_date < ?", (cutoff,)
        ),
        "open_flags_on_them": eng.db.scalar(
            "SELECT COUNT(*) FROM flags f JOIN invoices i ON i.id=f.subject_invoice_id WHERE f.status='OPEN'"
            " AND i.invoice_date < ?",
            (cutoff,),
        ),
    }


def retention_apply(eng: Engine, years: int, *, user_id: int, hard_delete: bool = False) -> dict[str, Any]:
    """Soft-delete (default) invoices older than the retention period. Hard delete purges them and
    their patients' encrypted names when no remaining invoice references them."""
    prev = retention_preview(eng, years)
    cutoff = prev["cutoff"]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with eng.db.tx() as c:
        ids = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM invoices WHERE deleted_at IS NULL AND invoice_date < ?", (cutoff,)
            ).fetchall()
        ]
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            ph = ",".join("?" * len(chunk))
            c.execute(f"UPDATE invoices SET deleted_at=? WHERE id IN ({ph})", [now, *chunk])
            c.execute(f"UPDATE invoice_lines SET deleted_at=? WHERE invoice_id IN ({ph})", [now, *chunk])
            c.execute(f"UPDATE flags SET active=0 WHERE subject_invoice_id IN ({ph})", chunk)
        purged = 0
        if hard_delete:
            purged = c.execute(
                "UPDATE patients SET first_name_enc=NULL, last_name_enc=NULL, deleted_at=? WHERE deleted_at IS NULL AND"
                " id NOT IN (SELECT DISTINCT patient_id FROM invoice_lines WHERE deleted_at IS NULL AND patient_id IS NOT NULL)",
                (now,),
            ).rowcount
        audit.record(
            eng.db,
            "RETENTION_APPLY",
            user_id=user_id,
            entity_type="retention",
            entity_id=cutoff,
            after={"invoices": len(ids), "hard_delete": hard_delete, "patients_purged": purged},
            conn=c,
        )
    if ids:
        eng.store.load_all()
    return {"cutoff": cutoff, "invoices_removed": len(ids), "patients_purged": purged}


def secure_remove_all(data_dir: Path, keystore_mode: str = "auto") -> None:
    """Uninstall 'Remove all data': overwrite the DB files, then delete them and the keys."""
    for p in list(data_dir.glob("verismo.db*")):
        try:
            size = p.stat().st_size
            with p.open("r+b") as f:
                remaining = size
                while remaining > 0:
                    n = min(remaining, 2**20)
                    f.write(secrets.token_bytes(n))
                    remaining -= n
                f.flush()
                os.fsync(f.fileno())
        finally:
            p.unlink(missing_ok=True)
    Keystore(data_dir, keystore_mode).delete()
    shutil.rmtree(data_dir / "logs", ignore_errors=True)


def backup_bytes_for_test(path: Path) -> io.BytesIO:  # pragma: no cover - helper for manual inspection
    return io.BytesIO(path.read_bytes())
