"""Versioned, audited settings. All rule thresholds live here and are admin-editable."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import Any

from invoice_analytics.db.connection import Database
from invoice_analytics.security import audit

DEFAULTS: dict[str, Any] = {
    # Invoice rules
    "rules.INV-001": {"enabled": True, "tier": "HARD"},
    "rules.INV-002": {"enabled": True, "tier": "HARD"},
    "rules.INV-003": {"enabled": True, "tier": "PROBABLE"},
    "rules.INV-004": {
        "enabled": True,
        "tier": "PROBABLE",
        "max_damerau": 1,
        "min_jaro_winkler": 0.92,
        "amount_tolerance_cents": 5,
        "min_length": 4,
        "sequential_gap": 3,
        "max_damerau_with_jaro_winkler": 2,
        "probable_max_date_gap_days": 3,
    },
    "rules.INV-005": {"enabled": True, "tier": "WEAK", "window_days": 45},
    "rules.INV-006": {"enabled": True, "tier": "PROBABLE", "min_overlap": 0.8},
    "rules.INV-007": {"enabled": True, "tier": "ESCALATE"},
    "rules.INV-008": {
        "enabled": True,
        "tier": "WEAK",
        "window_days": 30,
        "tolerance_cents": 100,
        "max_parts": 4,
        "max_candidates": 20,
    },
    # Clinical rules
    "rules.CLN-001": {"enabled": True, "tier": "HARD"},
    "rules.CLN-002": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-003": {"enabled": True, "tier": "WEAK", "day_window": 1},
    "rules.CLN-004": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-005": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-006": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-007": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-008": {"enabled": True, "tier": "WEAK", "min_cosine": 0.90},
    "rules.CLN-009": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-010": {"enabled": True, "tier": "PROBABLE"},
    "rules.CLN-011": {"enabled": True, "tier": "PROBABLE", "window_days": 30, "min_charge_cents": 100000},
    "rules.CLN-012": {"enabled": True, "tier": "PROBABLE"},
    # Suppressions
    "suppress.SUP-001": {"enabled": True, "net_tolerance_cents": 0},
    "suppress.SUP-002": {"enabled": True},
    "suppress.SUP-003": {"enabled": True},
    "suppress.SUP-004": {"enabled": True, "interval_slack_days": 1},
    "suppress.SUP-005": {"enabled": True},
    "suppress.SUP-006": {"enabled": True},
    # Identity resolution
    "linkage.patient": {"auto_link_threshold": 0.995, "review_threshold": 0.80},
    "linkage.party": {"suggest_threshold": 0.85},
    # AI
    "ai.tier": "OFF",  # OFF | LITE | STANDARD | PLUS
    "ai.model_path": "",
    "ai.idle_stop_seconds": 600,
    "ai.auto_explain": True,
    "ai.auto_triage": True,
    "ai.max_background_jobs_per_run": 200,
    "ai.extract_confidence_threshold": 0.85,
    # Ingest
    "ingest.watched_folders": [],
    "ingest.date_order": "US",
    # Security
    "security.session_idle_seconds": 900,
    # Demo/desktop mode: the app opens signed in as the primary admin (no login screen, no idle
    # lock). Turn on for deployments that handle real PHI (spec 9: access control, auto-lock).
    "security.require_login": False,
    "backup.folder": "",
    "retention.years": 7,
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Settings:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._cache is None:
            vals = copy.deepcopy(DEFAULTS)
            for r in self.db.query("SELECT key, value FROM settings"):
                vals[r["key"]] = json.loads(r["value"])
            self._cache = vals
        return self._cache

    def invalidate(self) -> None:
        self._cache = None

    def get(self, key: str, default: Any = None) -> Any:
        v = self._load().get(key, default)
        return copy.deepcopy(v)

    def all(self) -> dict[str, Any]:
        return copy.deepcopy(self._load())

    def version(self, key: str) -> int:
        v = self.db.scalar("SELECT version FROM settings WHERE key=?", (key,))
        return int(v or 0)

    def snapshot_version(self) -> str:
        """A compact identifier for the full settings state (stored with detection runs)."""
        v = self.db.scalar("SELECT COALESCE(SUM(version),0) || '.' || COUNT(*) FROM settings")
        return str(v)

    def set(self, key: str, value: Any, user_id: int | None = None) -> int:
        if key not in DEFAULTS and not key.startswith(("rules.", "suppress.", "ui.")):
            raise KeyError(f"unknown setting {key}")
        before = self.get(key)
        if isinstance(DEFAULTS.get(key), dict) and isinstance(value, dict):
            merged = {**(before or {}), **value}
            value = merged
        payload = json.dumps(value, sort_keys=True)
        ts = _now()
        with self.db.tx() as c:
            row = c.execute("SELECT version FROM settings WHERE key=?", (key,)).fetchone()
            ver = (row["version"] + 1) if row else 1
            c.execute(
                "INSERT INTO settings(key,value,version,updated_by,updated_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, version=excluded.version,"
                " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (key, payload, ver, user_id, ts),
            )
            c.execute(
                "INSERT INTO settings_history(key,value,version,updated_by,updated_at) VALUES (?,?,?,?,?)",
                (key, payload, ver, user_id, ts),
            )
            audit.record(
                self.db,
                "SETTINGS_CHANGE",
                user_id=user_id,
                entity_type="setting",
                entity_id=key,
                before=before,
                after=value,
                conn=c,
            )
        self.invalidate()
        return ver
