"""Runtime configuration: data locations and environment-provided launch parameters."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def default_user_data_dir() -> Path:
    """%LOCALAPPDATA%\\InvoiceAnalytics on Windows; platform equivalents elsewhere (dev only)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "InvoiceAnalytics"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "InvoiceAnalytics"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "invoice-analytics"


def default_shared_data_dir() -> Path:
    """%PROGRAMDATA%\\InvoiceAnalytics for shared reference data and models."""
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "InvoiceAnalytics"
    return default_user_data_dir() / "shared"


@dataclass
class Config:
    data_dir: Path = field(default_factory=default_user_data_dir)
    shared_dir: Path = field(default_factory=default_shared_data_dir)
    port: int = 0
    token: str = ""
    keystore: str = "auto"  # auto | dpapi | keyring | file (file = insecure, tests/dev only)
    allowed_origins: tuple[str, ...] = (
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
    )
    start_workers: bool = True
    session_idle_seconds: int = 15 * 60

    @property
    def db_path(self) -> Path:
        return self.data_dir / "invoice-analytics.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def models_dir(self) -> Path:
        return Path(os.environ.get("IA_MODELS_DIR", str(self.shared_dir / "models")))

    @classmethod
    def from_env(cls) -> Config:
        cfg = cls()
        if v := os.environ.get("IA_DATA_DIR"):
            cfg.data_dir = Path(v)
            cfg.shared_dir = Path(os.environ.get("IA_SHARED_DIR", str(Path(v) / "shared")))
        elif v := os.environ.get("IA_SHARED_DIR"):
            cfg.shared_dir = Path(v)
        cfg.port = int(os.environ.get("IA_PORT", "0") or 0)
        cfg.token = os.environ.get("IA_TOKEN", "")
        cfg.keystore = os.environ.get("IA_KEYSTORE", "auto")
        if extra := os.environ.get("IA_ALLOWED_ORIGINS"):
            cfg.allowed_origins = cfg.allowed_origins + tuple(extra.split(","))
        cfg.start_workers = os.environ.get("IA_NO_WORKERS", "") != "1"
        return cfg
