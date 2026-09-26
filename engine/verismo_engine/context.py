"""Engine runtime context: keys, database, settings and helpers shared by all modules."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from verismo_engine import ENGINE_VERSION
from verismo_engine.config import Config
from verismo_engine.db.connection import Database
from verismo_engine.db.migrate import migrate
from verismo_engine.security.crypto import FieldCipher, KeySet, keyed_hash
from verismo_engine.security.keystore import Keystore
from verismo_engine.settings import Settings

if TYPE_CHECKING:
    from verismo_engine.ai.jobs import JobQueue
    from verismo_engine.scoring.store import DetectionStore

log = logging.getLogger("verismo.context")


@dataclass
class Engine:
    config: Config
    keys: KeySet
    db: Database
    settings: Settings
    field_cipher: FieldCipher
    first_run: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
    _store: DetectionStore | None = None
    _jobs: JobQueue | None = None

    engine_version: str = ENGINE_VERSION

    def hmac(self, value: str, domain: str) -> str:
        return keyed_hash(self.keys.hmac_key, value, domain)

    @property
    def store(self) -> DetectionStore:
        if self._store is None:
            from verismo_engine.scoring.store import DetectionStore

            self._store = DetectionStore(self)
        return self._store

    @property
    def jobs(self) -> JobQueue:
        if self._jobs is None:
            from verismo_engine.ai.jobs import JobQueue

            self._jobs = JobQueue(self)
        return self._jobs

    def refdata_version(self) -> str:
        rows = self.db.query(
            "SELECT dataset, MAX(version) AS v FROM ref_dataset_versions GROUP BY dataset ORDER BY dataset"
        )
        return ";".join(f"{r['dataset']}={r['v']}" for r in rows) or "none"

    def close(self) -> None:
        if self._jobs is not None:
            self._jobs.stop()
        if self._store is not None:
            self._store.close()
        self.db.close()


def _backup_before_migrating(db: Database, config: Config) -> None:
    """Spec 12: an upgrade that changes the schema takes a backup first. The copy is the encrypted
    database file itself (same key), written next to the data as backups/pre-migrate-<ts>.db."""
    import shutil
    from datetime import UTC, datetime

    from verismo_engine.db.migrate import pending

    todo = pending(db)
    has_schema = db.scalar("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
    if not todo or not has_schema:
        return
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dest = config.data_dir / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(config.db_path, dest / f"pre-migrate-{ts}.db")
    log.info("pre-migration backup written before applying %d migration(s)", len(todo))


def open_engine(config: Config) -> Engine:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    ks = Keystore(config.data_dir, config.keystore)
    keys, created = ks.load_or_create()
    db = Database(config.db_path, keys.db_key)
    _backup_before_migrating(db, config)
    applied = migrate(db)
    if applied:
        log.info("applied migrations: %s", ",".join(applied))
    eng = Engine(
        config=config,
        keys=keys,
        db=db,
        settings=Settings(db),
        field_cipher=FieldCipher(keys.field_key),
        first_run=created,
    )
    return eng
