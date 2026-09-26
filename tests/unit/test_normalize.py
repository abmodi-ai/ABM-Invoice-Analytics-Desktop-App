"""Property-based and example tests for the normalization library (spec 5.2)."""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from invoice_analytics.normalize import (
    AmbiguousDateError,
    AmountError,
    DateError,
    classify_code,
    format_cents,
    normalize_description,
    normalize_invoice_number,
    normalize_modifiers,
    normalize_npi,
    normalize_party_name,
    normalize_person_name,
    npi_is_valid,
    ocr_fold,
    parse_amount,
    parse_date,
)
from invoice_analytics.normalize.identifiers import npi_check_digit

cents_st = st.integers(min_value=-(10**11), max_value=10**11)


# ---------------------------------------------------------------- amounts
@given(cents_st)
def test_amount_roundtrip_formatted(c: int) -> None:
    assert parse_amount(format_cents(c)) == c


@given(st.integers(min_value=0, max_value=10**11))
def test_amount_negative_forms_agree(c: int) -> None:
    body = f"{c // 100}.{c % 100:02d}"
    assert parse_amount(f"({body})") == -c
    assert parse_amount(f"{body}-") == -c
    assert parse_amount(f"-{body}") == -c
    assert parse_amount(f"${body}") == c


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=100, max_value=999))
def test_amount_rejects_three_decimals(whole: int, frac: int) -> None:
    with pytest.raises(AmountError):
        parse_amount(f"{whole}.{frac}")


@pytest.mark.parametrize("bad", ["", "  ", "1.234,56", "12,34", "1,2345.00", "abc", "--5", "(5)-", "1.2.3"])
def test_amount_rejects_ambiguous(bad: str) -> None:
    with pytest.raises(AmountError):
        parse_amount(bad)


@pytest.mark.parametrize(
    "raw,cents",
    [
        ("123.45", 12345),
        ("(123.45)", -12345),
        ("123.45-", -12345),
        ("$1,234.50", 123450),
        ("1,234", 123400),
        (".5", 50),
        ("USD 10", 1000),
        ("10.00 CR", -1000),
        (7, 700),
    ],
)
def test_amount_examples(raw: str, cents: int) -> None:
    assert parse_amount(raw) == cents


# ---------------------------------------------------------------- dates
dates_st = st.dates(min_value=dt.date(1901, 1, 1), max_value=dt.date(2099, 12, 31))


@given(dates_st)
def test_date_iso_roundtrip(d: dt.date) -> None:
    assert parse_date(d.isoformat()).iso == d.isoformat()
    assert parse_date(d.strftime("%Y%m%d")).iso == d.isoformat()


@given(dates_st)
def test_date_slash_never_guesses(d: dt.date) -> None:
    raw = f"{d.month:02d}/{d.day:02d}/{d.year}"
    if d.day <= 12 and d.day != d.month:
        with pytest.raises(AmbiguousDateError) as ei:
            parse_date(raw)
        assert d.isoformat() in ei.value.readings
    else:
        assert parse_date(raw).iso == d.isoformat()


@given(dates_st)
def test_date_non_strict_prefers_us(d: dt.date) -> None:
    raw = f"{d.month}/{d.day}/{d.year}"
    assert parse_date(raw, strict=False).iso == d.isoformat()


def test_date_examples() -> None:
    assert parse_date("Mar 4, 2026").iso == "2026-03-04"
    assert parse_date("04-MAR-2026").iso == "2026-03-04"
    assert parse_date("13/04/2026").iso == "2026-04-13"
    assert parse_date("2026-03-04").raw == "2026-03-04"
    with pytest.raises(DateError):
        parse_date("2026-02-30")
    with pytest.raises(DateError):
        parse_date("soon")


# ---------------------------------------------------------------- invoice numbers
inv_st = st.text(alphabet="ABCDEFGHJKMNPQRTUVWXY0123456789-_/.# ", min_size=1, max_size=16)


@given(inv_st)
def test_invoice_number_idempotent(raw: str) -> None:
    n = normalize_invoice_number(raw)
    assume(n is not None)
    assert normalize_invoice_number(n) == n


@given(inv_st)
def test_invoice_number_separator_insensitive(raw: str) -> None:
    assert normalize_invoice_number(raw) == normalize_invoice_number(raw.replace("-", " / "))


@given(st.text(alphabet="0123456789OISLBZG", min_size=1, max_size=12))
def test_ocr_fold_is_idempotent_and_digit_only(raw: str) -> None:
    f = ocr_fold(raw)
    assert f is not None and f.isdigit()
    assert ocr_fold(f) == f


def test_invoice_number_examples() -> None:
    assert normalize_invoice_number("INV-0045") == "45"
    assert normalize_invoice_number("Invoice #000123") == "123"
    assert normalize_invoice_number("inv 45") == normalize_invoice_number("INV_0045")
    assert normalize_invoice_number("ACME-77", ("ACME",)) == "77"
    assert normalize_invoice_number("NO.") == "NO"
    assert ocr_fold("I0O5") == "1005"


# ---------------------------------------------------------------- NPI
@given(st.text(alphabet="0123456789", min_size=9, max_size=9))
def test_npi_check_digit_valid(first9: str) -> None:
    npi = first9 + npi_check_digit(first9)
    assert npi_is_valid(npi)
    assert normalize_npi(f"{npi[:3]}-{npi[3:]}") == npi
    bad = npi[:-1] + str((int(npi[-1]) + 1) % 10)
    assert not npi_is_valid(bad)


def test_npi_known_valid() -> None:
    assert npi_is_valid("1234567893")  # CMS published example


# ---------------------------------------------------------------- names
@given(st.text(min_size=0, max_size=40))
def test_party_name_idempotent(raw: str) -> None:
    n = normalize_party_name(raw)
    assert normalize_party_name(n) == n


def test_party_name_examples() -> None:
    a = normalize_party_name("Acme Med. Ctr, Inc.")
    b = normalize_party_name("ACME MEDICAL CENTER LLC")
    assert a == b == "ACME MEDICAL CENTER"
    assert normalize_party_name("Smith & Jones Assoc") == "SMITH AND JONES ASSOCIATES"


@given(st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=1, max_size=20))
def test_person_name_case_insensitive(s: str) -> None:
    assert normalize_person_name(s.lower(), s.lower()) == normalize_person_name(s.upper(), s.upper())


def test_person_name_examples() -> None:
    n = normalize_person_name("José", "García-López Jr.")
    assert n.first == "JOSE"
    assert n.last == "GARCIA LOPEZ"
    assert n.last_tokens == ("GARCIA", "LOPEZ")
    assert normalize_person_name("Jon", "Smith").last_phonetic == normalize_person_name("J", "Smyth").last_phonetic


# ---------------------------------------------------------------- codes
@given(st.lists(st.sampled_from(["59", "25", "rt", "LT", "76", "xs", " gp "]), max_size=6))
def test_modifiers_sorted_set(mods: list[str]) -> None:
    out = normalize_modifiers(mods)
    assert out == sorted(set(out))
    assert normalize_modifiers(list(reversed(mods))) == out


def test_code_classification() -> None:
    assert classify_code("97110") == "CPT"
    assert classify_code("0001U") == "CPT"
    assert classify_code("J1100") == "HCPCS"
    assert classify_code("0450") == "REV"
    assert classify_code("W9999") == "OTHER"


def test_description_normalization() -> None:
    a = normalize_description("Ther exer, ea 15 min")
    b = normalize_description("THERAPEUTIC EXERCISE EACH 15 MINUTES")
    assert a == b
    assert "injection" in normalize_description("Inj, IM")
