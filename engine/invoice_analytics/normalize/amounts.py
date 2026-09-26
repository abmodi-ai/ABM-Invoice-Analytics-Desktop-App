"""Currency strings -> integer cents. Ambiguous values are rejected, never guessed."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


class AmountError(ValueError):
    pass


_CURRENCY = re.compile(r"^(USD|US\$|\$)|(USD)$", re.IGNORECASE)
_US_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d{1,2})?$")
_PLAIN = re.compile(r"^\d+(\.\d{1,2})?$")
_LEADING_DOT = re.compile(r"^\.\d{1,2}$")


def parse_amount(value: str | int | float | Decimal | None) -> int:
    """Parse to integer cents.

    Negative forms: leading '-', trailing '-', '(123.45)', 'CR' suffix.
    Rejected: more than 2 decimals, European '1.234,56' style, mixed separators, empty.
    """
    if value is None:
        raise AmountError("empty amount")
    if isinstance(value, bool):
        raise AmountError("boolean is not an amount")
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        value = repr(value)
    if isinstance(value, Decimal):
        value = str(value)
    s = str(value).strip().replace(" ", " ")
    if not s:
        raise AmountError("empty amount")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    if s.upper().endswith("CR"):
        neg, s = True, s[:-2].strip()
    if s.endswith("-"):
        if neg:
            raise AmountError(f"double negative in {value!r}")
        neg, s = True, s[:-1].strip()
    if s.startswith("-"):
        if neg:
            raise AmountError(f"double negative in {value!r}")
        neg, s = True, s[1:].strip()
    s = _CURRENCY.sub("", s).strip()
    if s.startswith("-"):
        if neg:
            raise AmountError(f"double negative in {value!r}")
        neg, s = True, s[1:].strip()
    s = s.replace(" ", "")
    if _LEADING_DOT.match(s):
        s = "0" + s
    if _US_GROUPED.match(s):
        s = s.replace(",", "")
    elif not _PLAIN.match(s):
        raise AmountError(f"ambiguous or invalid amount {value!r}")
    try:
        d = Decimal(s)
    except InvalidOperation as e:  # pragma: no cover - regex already guards
        raise AmountError(str(e)) from e
    cents = int((d * 100).to_integral_value())
    return -cents if neg else cents


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100:,}.{c % 100:02d}"
