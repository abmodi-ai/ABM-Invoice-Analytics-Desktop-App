"""CSV / Excel importer driven by a column mapping (canonical field -> source column).

Each row is one invoice line (or a header-only invoice when no line fields are mapped).
Rows are grouped into invoices by (party, invoice number, invoice date).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz, process

from invoice_analytics.ingest.model import (
    InvoiceIn,
    LineIn,
    ParseIssue,
    ParseResult,
    PartyIn,
    PatientIn,
)
from invoice_analytics.normalize import AmbiguousDateError, AmountError, DateError, parse_amount, parse_date
from invoice_analytics.normalize.names import split_full_name

HEADER_FIELDS = [
    "direction",
    "party_name",
    "party_type",
    "party_tax_id",
    "party_npi",
    "party_address",
    "party_phone",
    "invoice_number",
    "invoice_date",
    "due_date",
    "invoice_total",
    "currency",
    "po_number",
    "claim_type",
    "claim_frequency_code",
    "original_invoice_ref",
    "status",
]
LINE_FIELDS = [
    "line_no",
    "patient_first",
    "patient_last",
    "patient_full_name",
    "patient_dob",
    "patient_sex",
    "patient_zip",
    "member_id",
    "mrn",
    "account",
    "dos_from",
    "dos_to",
    "code",
    "modifiers",
    "modifier_1",
    "modifier_2",
    "modifier_3",
    "modifier_4",
    "units",
    "charge",
    "allowed",
    "paid",
    "rendering_npi",
    "place_of_service",
    "revenue_code",
    "dx_codes",
    "description",
]
CANONICAL_FIELDS = HEADER_FIELDS + LINE_FIELDS
REQUIRED = ["invoice_number"]

SYNONYMS: dict[str, list[str]] = {
    "direction": ["direction", "ap ar", "type"],
    "party_name": [
        "vendor",
        "vendor name",
        "supplier",
        "customer",
        "payer",
        "payer name",
        "party",
        "provider name",
        "billing provider",
        "biller",
        "company",
    ],
    "party_type": ["party type", "entity type"],
    "party_tax_id": ["tax id", "tin", "ein", "federal tax id", "vendor tax id"],
    "party_npi": ["billing npi", "provider npi", "group npi", "npi"],
    "party_address": ["remit address", "remit to", "address", "vendor address", "remittance address"],
    "party_phone": ["phone", "vendor phone", "telephone"],
    "invoice_number": [
        "invoice number",
        "invoice no",
        "invoice #",
        "inv no",
        "invoice",
        "inv #",
        "claim number",
        "claim id",
        "claim #",
        "bill number",
        "document number",
    ],
    "invoice_date": ["invoice date", "inv date", "bill date", "date", "claim date", "statement date"],
    "due_date": ["due date", "payment due"],
    "invoice_total": [
        "invoice total",
        "total",
        "amount",
        "invoice amount",
        "total charges",
        "claim total",
        "total amount",
        "amount due",
    ],
    "currency": ["currency", "ccy"],
    "po_number": ["po number", "po", "purchase order"],
    "claim_type": ["claim type"],
    "claim_frequency_code": ["frequency code", "claim frequency", "freq code", "clm05 3"],
    "original_invoice_ref": ["original claim", "original invoice", "original ref", "payer claim control number"],
    "status": ["status", "invoice status", "payment status"],
    "line_no": ["line", "line no", "line number", "line #", "seq"],
    "patient_first": ["patient first", "first name", "patient first name", "pt first"],
    "patient_last": ["patient last", "last name", "patient last name", "pt last"],
    "patient_full_name": ["patient", "patient name", "member name", "pt name"],
    "patient_dob": ["dob", "date of birth", "birth date", "patient dob"],
    "patient_sex": ["sex", "gender"],
    "patient_zip": ["zip", "zip code", "postal code", "patient zip"],
    "member_id": ["member id", "subscriber id", "insurance id", "policy number"],
    "mrn": ["mrn", "medical record number", "chart number"],
    "account": ["account", "account number", "patient account"],
    "dos_from": ["dos", "date of service", "service date", "dos from", "from date"],
    "dos_to": ["dos to", "to date", "service end"],
    "code": ["cpt", "hcpcs", "cpt code", "procedure code", "code", "cpt hcpcs", "proc code"],
    "modifiers": ["modifiers", "modifier", "mods"],
    "modifier_1": ["modifier 1", "mod 1", "mod1"],
    "modifier_2": ["modifier 2", "mod 2", "mod2"],
    "modifier_3": ["modifier 3", "mod 3", "mod3"],
    "modifier_4": ["modifier 4", "mod 4", "mod4"],
    "units": ["units", "qty", "quantity", "days units"],
    "charge": ["charge", "line charge", "charges", "billed", "billed amount", "line amount", "extended amount"],
    "allowed": ["allowed", "allowed amount"],
    "paid": ["paid", "paid amount", "payment"],
    "rendering_npi": ["rendering npi", "rendering provider npi", "performing npi"],
    "place_of_service": ["pos", "place of service"],
    "revenue_code": ["revenue code", "rev code"],
    "dx_codes": ["diagnosis", "dx", "icd", "diagnosis codes", "icd10"],
    "description": ["description", "service description", "item description", "memo", "line description"],
}


def _norm_header(h: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9#]+", " ", h.lower()).split())


def header_signature(headers: list[str]) -> str:
    return hashlib.sha256("|".join(_norm_header(h) for h in headers).encode()).hexdigest()


def suggest_mapping(headers: list[str]) -> dict[str, dict[str, Any]]:
    """Best source column per canonical field, with a confidence 0..100."""
    normed = {_norm_header(h): h for h in headers}
    out: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    # exact synonym hits first, then fuzzy
    for fld, syns in SYNONYMS.items():
        for s in syns:
            if s in normed and normed[s] not in taken:
                out[fld] = {"column": normed[s], "confidence": 100}
                taken.add(normed[s])
                break
    for fld, syns in SYNONYMS.items():
        if fld in out:
            continue
        choices = [n for n, orig in normed.items() if orig not in taken]
        best: tuple[str, float] | None = None
        for s in syns:
            m = process.extractOne(s, choices, scorer=fuzz.token_sort_ratio)
            if m and (best is None or m[1] > best[1]):
                best = (m[0], m[1])
        if best and best[1] >= 85:
            out[fld] = {"column": normed[best[0]], "confidence": int(best[1])}
            taken.add(normed[best[0]])
    return out


@dataclass
class CsvOptions:
    direction: str = "AP"
    party_type: str = "VENDOR"
    party_name: str | None = None  # default when the file is from a single party
    date_order: str = "US"  # US | DMY
    strict_dates: bool = True
    sheet: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> CsvOptions:
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)


def read_table(data: bytes, filename: str, sheet: str | None = None) -> Table:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - corrupt workbooks
            raise MalformedFile(f"could not open workbook: {type(e).__name__}") from e
        ws = wb[sheet] if sheet else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else f"col{i}" for i, h in enumerate(next(it, []))]
        rows = []
        for r in it:
            if r is None or all(v is None or str(v).strip() == "" for v in r):
                continue
            rows.append(["" if v is None else (v.isoformat()[:10] if hasattr(v, "isoformat") else str(v)) for v in r])
        return Table(headers, rows)
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        reader = csv.reader(io.StringIO(text, newline=""), dialect)
        headers = [h.strip() for h in next(reader, [])]
        rows = [r for r in reader if any(c.strip() for c in r)]
    except csv.Error as e:
        raise MalformedFile(f"malformed CSV: {e}") from e
    return Table(headers, rows)


class MalformedFile(ValueError):
    pass


def _cell(row: list[str], idx: dict[str, int], fld: str) -> str | None:
    i = idx.get(fld)
    if i is None or i >= len(row):
        return None
    v = row[i].strip()
    return v or None


def parse_table(table: Table, mapping: dict[str, str], options: CsvOptions) -> ParseResult:
    res = ParseResult(method="CSV")
    col_index = {h: i for i, h in enumerate(table.headers)}
    idx: dict[str, int] = {}
    for fld, col in mapping.items():
        if fld not in CANONICAL_FIELDS:
            res.issues.append(ParseIssue("mapping", f"unknown field {fld}", "WARNING"))
            continue
        if col not in col_index:
            res.issues.append(ParseIssue("mapping", f"column {col!r} not in file"))
            continue
        idx[fld] = col_index[col]
    for r in REQUIRED:
        if r not in idx:
            res.issues.append(ParseIssue("mapping", f"required field {r} is not mapped"))
    if "party_name" not in idx and not options.party_name:
        res.issues.append(ParseIssue("mapping", "party_name is not mapped and no default party given"))
    if res.errors:
        return res
    has_lines = any(f in idx for f in ("code", "charge", "dos_from", "description"))
    groups: OrderedDict[tuple[str, str, str], InvoiceIn] = OrderedDict()
    line_counters: dict[tuple[str, str, str], int] = {}

    def date(v: str | None, where: str, attn: list[str] | None = None) -> str | None:
        if not v:
            return None
        try:
            return parse_date(v, prefer=options.date_order, strict=options.strict_dates).iso
        except AmbiguousDateError as e:
            msg = f"{where}: {e}"
            if attn is not None:
                attn.append(msg)
            res.issues.append(ParseIssue(where, str(e), "WARNING"))
            return None
        except DateError as e:
            res.issues.append(ParseIssue(where, str(e)))
            return None

    def amount(v: str | None, where: str) -> int | None:
        if v is None:
            return None
        try:
            return parse_amount(v)
        except AmountError as e:
            res.issues.append(ParseIssue(where, str(e)))
            return None

    for n, row in enumerate(table.rows, start=2):
        loc = f"row {n}"
        num = _cell(row, idx, "invoice_number")
        if not num:
            res.issues.append(ParseIssue(loc, "missing invoice number"))
            continue
        party_name = _cell(row, idx, "party_name") or options.party_name or ""
        raw_date = _cell(row, idx, "invoice_date")
        key = (party_name.upper(), num.upper(), raw_date or "")
        inv = groups.get(key)
        if inv is None:
            attn: list[str] = []
            direction = (_cell(row, idx, "direction") or options.direction).upper()
            ptype = (_cell(row, idx, "party_type") or options.party_type).upper()
            total = amount(_cell(row, idx, "invoice_total"), f"{loc} invoice_total")
            inv = InvoiceIn(
                direction=direction if direction in ("AP", "AR") else options.direction,
                party=PartyIn(
                    name=party_name,
                    party_type=ptype if ptype in ("VENDOR", "CUSTOMER", "PAYER") else options.party_type,
                    tax_id=_cell(row, idx, "party_tax_id"),
                    npi=_cell(row, idx, "party_npi"),
                    address=_cell(row, idx, "party_address"),
                    phone=_cell(row, idx, "party_phone"),
                ),
                invoice_number=num,
                invoice_date=date(raw_date, f"{loc} invoice_date", attn),
                invoice_date_raw=raw_date,
                due_date=date(_cell(row, idx, "due_date"), f"{loc} due_date"),
                total_cents=total,
                currency=(_cell(row, idx, "currency") or "USD").upper()[:3],
                po_number=_cell(row, idx, "po_number"),
                claim_type=_claim_type(_cell(row, idx, "claim_type")),
                claim_frequency_code=_cell(row, idx, "claim_frequency_code"),
                original_invoice_ref=_cell(row, idx, "original_invoice_ref"),
                status=(_cell(row, idx, "status") or "OPEN").upper(),
                needs_attention=attn,
                source_ref=loc,
            )
            groups[key] = inv
            line_counters[key] = 0
        if not has_lines:
            continue
        line_counters[key] += 1
        full = _cell(row, idx, "patient_full_name")
        first, last = _cell(row, idx, "patient_first"), _cell(row, idx, "patient_last")
        if full and not last:
            first, last = split_full_name(full)
        patient = PatientIn(
            first=first,
            last=last,
            dob=date(_cell(row, idx, "patient_dob"), f"{loc} patient_dob"),
            sex=_cell(row, idx, "patient_sex"),
            zip=_cell(row, idx, "patient_zip"),
            member_id=_cell(row, idx, "member_id"),
            mrn=_cell(row, idx, "mrn"),
            account=_cell(row, idx, "account"),
        )
        mods: list[str] = []
        if m := _cell(row, idx, "modifiers"):
            mods.extend(re.split(r"[\s,;:|/]+", m))
        for k in ("modifier_1", "modifier_2", "modifier_3", "modifier_4"):
            if m := _cell(row, idx, k):
                mods.append(m)
        units_raw = _cell(row, idx, "units")
        try:
            units = float(units_raw) if units_raw else 1.0
        except ValueError:
            res.issues.append(ParseIssue(loc, f"invalid units {units_raw!r}"))
            units = 1.0
        ln_raw = _cell(row, idx, "line_no")
        dos_from = date(_cell(row, idx, "dos_from"), f"{loc} dos_from")
        inv.lines.append(
            LineIn(
                line_no=int(ln_raw) if ln_raw and ln_raw.isdigit() else line_counters[key],
                patient=None if patient.is_empty() else patient,
                dos_from=dos_from,
                dos_to=date(_cell(row, idx, "dos_to"), f"{loc} dos_to") or dos_from,
                code=_cell(row, idx, "code"),
                modifiers=mods,
                units=units,
                charge_cents=amount(_cell(row, idx, "charge"), f"{loc} charge") or 0,
                allowed_cents=amount(_cell(row, idx, "allowed"), f"{loc} allowed"),
                paid_cents=amount(_cell(row, idx, "paid"), f"{loc} paid"),
                rendering_npi=_cell(row, idx, "rendering_npi"),
                place_of_service=_cell(row, idx, "place_of_service"),
                revenue_code=_cell(row, idx, "revenue_code"),
                dx_codes=(
                    re.split(r"[\s,;|]+", _cell(row, idx, "dx_codes") or "") if _cell(row, idx, "dx_codes") else []
                ),
                description=_cell(row, idx, "description"),
            )
        )
    # de-duplicate line numbers within an invoice (the DB enforces uniqueness)
    for inv in groups.values():
        seen: set[int] = set()
        nxt = max((li.line_no for li in inv.lines), default=0) + 1
        for li in inv.lines:
            if li.line_no in seen:
                li.line_no = nxt
                nxt += 1
            seen.add(li.line_no)
    res.invoices = list(groups.values())
    bad = sum(1 for i in res.issues if i.severity == "ERROR")
    res.confidence = max(0.0, 1.0 - bad / max(1, len(table.rows)))
    return res


def _claim_type(v: str | None) -> str | None:
    if not v:
        return None
    u = v.upper()
    if u.startswith(("P", "837P", "CMS")):
        return "PROFESSIONAL"
    if u.startswith(("I", "837I", "UB")):
        return "INSTITUTIONAL"
    return None
