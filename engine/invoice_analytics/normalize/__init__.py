"""Normalization library (spec section 5.2). Every function is pure and property-tested."""

from invoice_analytics.normalize.amounts import AmountError, format_cents, parse_amount
from invoice_analytics.normalize.codes import (
    classify_code,
    is_em_code,
    normalize_code,
    normalize_description,
    normalize_modifiers,
)
from invoice_analytics.normalize.dates import AmbiguousDateError, DateError, ParsedDate, parse_date
from invoice_analytics.normalize.identifiers import (
    normalize_address,
    normalize_invoice_number,
    normalize_npi,
    normalize_phone,
    normalize_tax_id,
    normalize_zip,
    npi_is_valid,
    ocr_fold,
)
from invoice_analytics.normalize.names import (
    canonical_first_name,
    normalize_party_name,
    normalize_person_name,
    phonetic,
)

__all__ = [
    "AmbiguousDateError",
    "AmountError",
    "DateError",
    "ParsedDate",
    "canonical_first_name",
    "classify_code",
    "format_cents",
    "is_em_code",
    "normalize_address",
    "normalize_code",
    "normalize_description",
    "normalize_invoice_number",
    "normalize_modifiers",
    "normalize_npi",
    "normalize_party_name",
    "normalize_person_name",
    "normalize_phone",
    "normalize_tax_id",
    "normalize_zip",
    "npi_is_valid",
    "ocr_fold",
    "parse_amount",
    "parse_date",
    "phonetic",
]
