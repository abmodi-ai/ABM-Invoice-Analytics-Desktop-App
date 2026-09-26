"""SQLCipher connection management.

One Database object per process. Each thread gets its own connection (sqlite connections are
not shareable across threads). A separate read-only connection factory backs NL query (T3).
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlcipher3

Row = dict[str, Any]


class NetworkPathError(RuntimeError):
    pass


_REMOTE_FS = {"smbfs", "nfs", "nfs4", "cifs", "afpfs", "webdav", "fuse.sshfs", "9p"}


def is_network_path(path: Path) -> bool:
    """SQLite on a network share is unsupported (D4). Detect and refuse."""
    p = str(path.resolve() if path.exists() else path.parent.resolve())
    if sys.platform == "win32":  # pragma: no cover - Windows only
        if p.startswith("\\\\"):
            return True
        import ctypes

        drive = p[:3]
        DRIVE_REMOTE = 4
        return bool(ctypes.windll.kernel32.GetDriveTypeW(drive) == DRIVE_REMOTE)  # type: ignore[attr-defined]
    try:
        import psutil

        best = ""
        fstype = ""
        for part in psutil.disk_partitions(all=True):
            mp = part.mountpoint
            if (p == mp or p.startswith(mp.rstrip("/") + "/")) and len(mp) > len(best):
                best, fstype = mp, part.fstype
        return fstype.lower() in _REMOTE_FS
    except Exception:  # noqa: BLE001
        return False


def _dict_factory(cursor: Any, row: Sequence[Any]) -> Row:
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


class Database:
    def __init__(self, path: Path, key: bytes, *, allow_network: bool = False) -> None:
        if not allow_network and is_network_path(path):
            raise NetworkPathError(f"database path {path} is on a network share; not supported")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._key_hex = key.hex()
        self._local = threading.local()
        self._all: list[Any] = []
        self._lock = threading.Lock()
        self.write_lock = threading.RLock()

    def _open(self, readonly: bool = False) -> Any:
        if readonly:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            conn = sqlcipher3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        else:
            conn = sqlcipher3.connect(str(self.path), check_same_thread=False, timeout=30)
        conn.execute(f"PRAGMA key = \"x'{self._key_hex}'\"")
        conn.execute("PRAGMA cipher_memory_security = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        if not readonly:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")  # no plaintext temp files
        conn.row_factory = _dict_factory
        return conn

    @property
    def conn(self) -> Any:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._open()
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    def open_readonly(self) -> Any:
        """A fresh read-only connection (caller closes). Used for NL query."""
        conn = self._open(readonly=True)
        conn.execute("PRAGMA query_only = ON")
        return conn

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Row | None:
        row: Row | None = self.conn.execute(sql, params).fetchone()
        return row

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        return next(iter(row.values()))

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """Serialized write transaction."""
        with self.write_lock:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                with contextlib.suppress(Exception):
                    c.close()
            self._all.clear()
        self._local = threading.local()
