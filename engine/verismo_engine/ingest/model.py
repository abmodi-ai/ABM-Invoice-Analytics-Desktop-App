"""Canonical parsed records produced by every importer before persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PartyIn:
    name: str
    party_type: str = "VENDOR"  # VENDOR | CUSTOMER | PAYER
    tax_id: str | None = None
    npi: str | None = None
    address: str | None = None
    phone: str | None = None


@dataclass
class PatientIn:
    first: str | None = None
    last: str | None = None
    dob: str | None = None
    sex: str | None = None
    zip: str | None = None
    member_id: str | None = None
    mrn: str | None = None
    account: str | None = None

    def is_empty(self) -> bool:
        return not (self.last or self.member_id or self.mrn)


@dataclass
class LineIn:
    line_no: int
    patient: PatientIn | None = None
    dos_from: str | None = None
    dos_to: str | None = None
    code: str | None = None
    modifiers: list[str] = field(default_factory=list)
    units: float = 1.0
    charge_cents: int = 0
    allowed_cents: int | None = None
    paid_cents: int | None = None
    rendering_npi: str | None = None
    place_of_service: str | None = None
    revenue_code: str | None = None
    dx_codes: list[str] = field(default_factory=list)
    description: str | None = None


@dataclass
class InvoiceIn:
    direction: str  # AP | AR
    party: PartyIn
    invoice_number: str | None
    invoice_date: str | None
    total_cents: int | None = None
    invoice_date_raw: str | None = None
    due_date: str | None = None
    currency: str = "USD"
    po_number: str | None = None
    claim_type: str | None = None  # PROFESSIONAL | INSTITUTIONAL
    claim_frequency_code: str | None = None
    original_invoice_ref: str | None = None
    status: str = "OPEN"
    lines: list[LineIn] = field(default_factory=list)
    needs_attention: list[str] = field(default_factory=list)
    source_ref: str | None = None  # row numbers / segment positions, for error messages

    def effective_total(self) -> int:
        if self.total_cents is not None:
            return self.total_cents
        return sum(li.charge_cents for li in self.lines)


@dataclass
class ParseIssue:
    location: str
    message: str
    severity: str = "ERROR"  # ERROR | WARNING

    def as_dict(self) -> dict[str, Any]:
        return {"location": self.location, "message": self.message, "severity": self.severity}


@dataclass
class ParseResult:
    method: str  # CSV | X12 | PDF_TEXT | OCR | LLM
    invoices: list[InvoiceIn] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    confidence: float = 1.0
    page_count: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[ParseIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]
