"""Structured JSON logs with no PHI. Code logs IDs only; this filter is a second line of defence
that scrubs identifier-shaped values, and a unit test drives a full ingest to prove no patient
names, DOBs or member IDs reach the log."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import UTC, datetime
from pathlib import Path

_SCRUB = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{2}-\d{7}\b"), "[TAXID]"),
    (re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b"), "[DATE]"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "[DATE]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b[A-Z]{1,3}\d{6,12}\b"), "[ID]"),
    (re.compile(r"(?i)\b(name|first|last|patient|dob|member|mrn)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
]


def scrub(msg: str) -> str:
    for rx, rep in _SCRUB:
        msg = rx.sub(rep, msg)
    return msg


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        record.msg = scrub(msg)
        record.args = ()
        if record.exc_info:
            # Exceptions can carry values; keep the type and the scrubbed message only.
            et, ev, _tb = record.exc_info
            record.exc_text = f"{et.__name__ if et else 'Exception'}: {scrub(str(ev))[:300]}"
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_text:
            d["exc"] = record.exc_text
        return json.dumps(d)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Handler:
    log_dir.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        log_dir / "engine.log", maxBytes=10 * 2**20, backupCount=5, encoding="utf-8"
    )
    h.addFilter(RedactionFilter())
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    for old in list(root.handlers):
        if getattr(old, "_invoice_analytics", False):
            root.removeHandler(old)
    h._invoice_analytics = True  # type: ignore[attr-defined]
    root.addHandler(h)
    for noisy in ("splink", "httpx", "httpcore", "uvicorn.access", "pdfminer", "fastembed"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return h
