"""Local users, roles and idle-expiring sessions."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from verismo_engine.db.connection import Database
from verismo_engine.security import audit
from verismo_engine.security.passwords import hash_password, needs_rehash, verify_password

ROLES = ("ADMIN", "REVIEWER", "VIEWER")
ROLE_RANK = {"VIEWER": 0, "REVIEWER": 1, "ADMIN": 2}


class AuthError(RuntimeError):
    pass


@dataclass
class Session:
    token: str
    user_id: int
    username: str
    display_name: str
    role: str
    last_seen: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
        }


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_user(
    db: Database, username: str, display_name: str, role: str, password: str, *, actor_id: int | None = None
) -> int:
    if role not in ROLES:
        raise ValueError(f"invalid role {role}")
    h = hash_password(password)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO users(username, display_name, role, password_hash) VALUES (?,?,?,?)",
            (username, display_name, role, h),
        )
        uid = int(cur.lastrowid)
        audit.record(
            db,
            "USER_CREATE",
            user_id=actor_id,
            entity_type="user",
            entity_id=uid,
            after={"username": username, "role": role},
            conn=c,
        )
    return uid


def user_count(db: Database) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM users") or 0)


class SessionManager:
    def __init__(self, db: Database, idle_seconds: int = 900) -> None:
        self.db = db
        self.idle_seconds = idle_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def login(self, username: str, password: str) -> Session:
        row = self.db.one("SELECT * FROM users WHERE username=? AND active=1", (username,))
        if row is None or not verify_password(row["password_hash"], password):
            audit.record(self.db, "LOGIN_FAILED", entity_type="user", entity_id=username)
            raise AuthError("invalid username or password")
        with self.db.tx() as c:
            if needs_rehash(row["password_hash"]):
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), row["id"]))
            c.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?", (_now(), _now(), row["id"]))
            audit.record(self.db, "LOGIN", user_id=row["id"], entity_type="user", entity_id=row["id"], conn=c)
        s = Session(
            secrets.token_urlsafe(32), row["id"], row["username"], row["display_name"], row["role"], time.monotonic()
        )
        with self._lock:
            self._sessions[s.token] = s
        return s

    def logout(self, token: str) -> None:
        with self._lock:
            s = self._sessions.pop(token, None)
        if s:
            audit.record(self.db, "LOGOUT", user_id=s.user_id, entity_type="user", entity_id=s.user_id)

    def get(self, token: str | None, *, touch: bool = True) -> Session:
        if not token:
            raise AuthError("not signed in")
        with self._lock:
            s = self._sessions.get(token)
            if s is None:
                raise AuthError("session expired")
            if time.monotonic() - s.last_seen > self.idle_seconds:
                del self._sessions[token]
                raise AuthError("session locked after inactivity")
            if touch:
                s.last_seen = time.monotonic()
            return s

    def require(self, token: str | None, min_role: str) -> Session:
        s = self.get(token)
        if ROLE_RANK[s.role] < ROLE_RANK[min_role]:
            raise PermissionError(f"requires {min_role}")
        return s
