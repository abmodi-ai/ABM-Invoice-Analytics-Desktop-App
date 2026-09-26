"""Date parsing to ISO. A date that could be read two ways is reported, never guessed."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass


class DateError(ValueError):
    pass


class AmbiguousDateError(DateError):
    def __init__(self, raw: str, readings: list[str]) -> None:
        super().__init__(f"ambiguous date {raw!r}: could be {' or '.join(readings)}")
        self.raw = raw
        self.readings = readings


@dataclass(frozen=True)
class ParsedDate:
    iso: str
    raw: str


_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("JAN", "JANUARY"),
            ("FEB", "FEBRUARY"),
            ("MAR", "MARCH"),
            ("APR", "APRIL"),
            ("MAY",),
            ("JUN", "JUNE"),
            ("JUL", "JULY"),
            ("AUG", "AUGUST"),
            ("SEP", "SEPT", "SEPTEMBER"),
            ("OCT", "OCTOBER"),
            ("NOV", "NOVEMBER"),
            ("DEC", "DECEMBER"),
        ],
        start=1,
    )
    for m in names
}

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_CCYYMMDD = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_SLASH = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
_TEXT_MDY = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_TEXT_DMY = re.compile(r"^(\d{1,2})[\s\-]([A-Za-z]{3,9})[\s\-,]+(\d{2}|\d{4})$")


def _mk(y: int, m: int, d: int) -> str | None:
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def _year(y: str, pivot: int = 50) -> int:
    if len(y) == 4:
        return int(y)
    yy = int(y)
    return 2000 + yy if yy < pivot else 1900 + yy


def parse_date(value: str | dt.date | None, *, prefer: str = "US", strict: bool = True) -> ParsedDate:
    """Parse a date string.

    `prefer="US"` reads 03/04/2026 as MM/DD. Where both MM/DD and DD/MM are valid and differ,
    `strict=True` raises AmbiguousDateError so the UI can ask the user. `strict=False` accepts
    the preferred reading (used only when a source template has declared its date order).
    """
    if value is None:
        raise DateError("empty date")
    if isinstance(value, dt.datetime):
        return ParsedDate(value.date().isoformat(), value.isoformat())
    if isinstance(value, dt.date):
        return ParsedDate(value.isoformat(), value.isoformat())
    raw = str(value)
    s = raw.strip()
    if not s:
        raise DateError("empty date")
    if m := _ISO.match(s):
        iso = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return ParsedDate(iso, raw)
        raise DateError(f"invalid date {raw!r}")
    if m := _CCYYMMDD.match(s):
        iso = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return ParsedDate(iso, raw)
        raise DateError(f"invalid date {raw!r}")
    if m := _SLASH.match(s):
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        mdy = _mk(y, a, b)
        dmy = _mk(y, b, a)
        if prefer == "DMY":
            mdy, dmy = dmy, mdy
        if mdy and dmy and mdy != dmy:
            if strict:
                raise AmbiguousDateError(raw, [mdy, dmy])
            return ParsedDate(mdy, raw)
        if mdy:
            return ParsedDate(mdy, raw)
        if dmy:
            return ParsedDate(dmy, raw)
        raise DateError(f"invalid date {raw!r}")
    if m := _TEXT_MDY.match(s):
        mon = _MONTHS.get(m.group(1).upper())
        if mon:
            iso = _mk(int(m.group(3)), mon, int(m.group(2)))
            if iso:
                return ParsedDate(iso, raw)
    if m := _TEXT_DMY.match(s):
        mon = _MONTHS.get(m.group(2).upper())
        if mon:
            iso = _mk(_year(m.group(3)), mon, int(m.group(1)))
            if iso:
                return ParsedDate(iso, raw)
    raise DateError(f"unrecognized date {raw!r}")


def days_between(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
