"""Invoice numbers, tax IDs, NPIs, phones and addresses."""

from __future__ import annotations

import re

_INV_STRIP = re.compile(r"[\s\-_/.#]+")
_DEFAULT_PREFIXES = ("INVOICE", "INV", "NO", "NUM", "NBR")
_OCR_FOLD = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})


def normalize_invoice_number(raw: str | None, party_prefixes: tuple[str, ...] = ()) -> str | None:
    """Uppercase; strip whitespace - _ / . #; strip known prefixes; strip leading zeros."""
    if raw is None:
        return None
    s = _INV_STRIP.sub("", str(raw).upper())
    if not s:
        return None
    prefixes = sorted(set(_DEFAULT_PREFIXES) | {p.upper() for p in party_prefixes}, key=len, reverse=True)
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            # Only strip a prefix when something alphanumeric remains after it. Prefixes are matched
            # OCR-tolerantly ("1NV" is "INV" read by a scanner).
            if len(s) > len(p) and (s.startswith(p) or _fold_all(s[: len(p)]) == _fold_all(p)):
                s = s[len(p) :]
                changed = True
                break
    stripped = s.lstrip("0")
    return stripped or "0"


def _fold_all(s: str) -> str:
    return s.upper().translate(_OCR_FOLD)


def ocr_fold(norm: str | None) -> str | None:
    """Collapse glyphs OCR commonly confuses (O->0, I/L->1, S->5, B->8, Z->2, G->6)."""
    if norm is None:
        return None
    folded = norm.upper().translate(_OCR_FOLD)
    return folded.lstrip("0") or "0"


def digits_only(raw: str | None) -> str:
    return re.sub(r"\D", "", raw or "")


def normalize_tax_id(raw: str | None) -> str | None:
    d = digits_only(raw)
    return d if len(d) == 9 else None


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def npi_is_valid(npi: str) -> bool:
    """NPI check digit: Luhn over '80840' + the 10-digit NPI."""
    return len(npi) == 10 and npi.isdigit() and _luhn_ok("80840" + npi)


def npi_check_digit(first9: str) -> str:
    for d in "0123456789":
        if _luhn_ok("80840" + first9 + d):
            return d
    raise AssertionError("unreachable")  # pragma: no cover


def normalize_npi(raw: str | None) -> str | None:
    d = digits_only(raw)
    return d if npi_is_valid(d) else None


def normalize_phone(raw: str | None) -> str | None:
    d = digits_only(raw)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else None


_ADDR_ABBR = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "ROAD": "RD",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "SUITE": "STE",
    "LANE": "LN",
    "COURT": "CT",
    "PLACE": "PL",
    "HIGHWAY": "HWY",
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "FLOOR": "FL",
    "BUILDING": "BLDG",
    "PARKWAY": "PKWY",
    "CIRCLE": "CIR",
    "POBOX": "PO BOX",
}


def normalize_address(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^A-Z0-9 ]", " ", raw.upper())
    s = re.sub(r"\bP\s*O\s+BOX\b", "POBOX", s)
    toks = [_ADDR_ABBR.get(t, t) for t in s.split()]
    out = " ".join(toks)
    return out or None


def normalize_zip(raw: str | None) -> str | None:
    d = digits_only(raw)
    return d[:5] if len(d) >= 5 else None
