"""Plain versioned SQL migrations (NNNN_name.sql), applied in order, each in one transaction."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from verismo_engine.db.connection import Database

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


def _migration_files() -> list[tuple[int, str, str]]:
    base = resources.files("verismo_engine.db") / "migrations"
    out = []
    for entry in base.iterdir():
        m = _NAME.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    out.sort()
    versions = [v for v, _, _ in out]
    if len(versions) != len(set(versions)):
        raise MigrationError("duplicate migration version")
    return out


def _split_statements(sql: str) -> list[str]:
    """Split on ';' at statement level, keeping CREATE TRIGGER ... END; blocks intact."""
    stmts: list[str] = []
    buf: list[str] = []
    in_trigger = False
    for line in sql.splitlines():
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.startswith("CREATE TRIGGER"):
            in_trigger = True
        if in_trigger:
            if upper.endswith("END;"):
                stmts.append("\n".join(buf))
                buf, in_trigger = [], False
        elif stripped.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    if buf and "".join(buf).strip():
        stmts.append("\n".join(buf))
    return stmts


def pending(db: Database) -> list[str]:
    """Names of migrations not yet applied (empty for a brand-new database too, see caller)."""
    exists = db.scalar("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
    applied = {r["version"] for r in db.query("SELECT version FROM schema_migrations")} if exists else set()
    return [name for version, name, _sql in _migration_files() if version not in applied]


def migrate(db: Database) -> list[str]:
    """Apply pending migrations. Returns names applied. Refuses if an applied file changed."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    applied = {r["version"]: r for r in db.query("SELECT * FROM schema_migrations")}
    done: list[str] = []
    for version, name, sql in _migration_files():
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if version in applied:
            if applied[version]["sha256"] != digest:
                raise MigrationError(f"applied migration {name} was modified")
            continue
        with db.tx() as c:
            for stmt in _split_statements(sql):
                c.execute(stmt)
            c.execute(
                "INSERT INTO schema_migrations(version, name, sha256, applied_at) VALUES (?,?,?,?)",
                (version, name, digest, datetime.now(UTC).isoformat()),
            )
        done.append(name)
    return done


def current_version(db: Database) -> int:
    v = db.scalar("SELECT MAX(version) FROM schema_migrations")
    return int(v or 0)


def migrations_dir() -> Path:
    return Path(str(resources.files("verismo_engine.db") / "migrations"))
