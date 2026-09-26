"""Thin X12 5010 parser for 837P, 837I and 835.

Covers the ISA/GS/ST envelope and the loops the detection engine needs:
  837: 2000A/2010AA billing provider, 2000B/2010BA subscriber, 2010BB payer, 2000C/2010CA patient,
       2300 CLM (+DTP, REF*F8, HI, NM1*82 rendering), 2400 LX/SV1/SV2/DTP*472 (+2420A NM1*82).
  835: N1*PR payer, CLP claim payment, NM1*QC patient, SVC service payment, DTM*472.
Anything else is skipped. The parser never executes or evaluates content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from invoice_analytics.ingest.model import InvoiceIn, LineIn, ParseIssue, ParseResult, PartyIn, PatientIn
from invoice_analytics.normalize import AmountError, DateError, parse_amount, parse_date


class X12Error(ValueError):
    pass


@dataclass
class Delims:
    element: str
    component: str
    segment: str
    repetition: str


def detect_delimiters(text: str) -> Delims:
    t = text.lstrip("﻿ \r\n\t")
    if not t.startswith("ISA") or len(t) < 106:
        raise X12Error("not an X12 interchange (missing ISA header)")
    el = t[3]
    parts = t[:106].split(el)
    if len(parts) < 17:
        raise X12Error("malformed ISA segment")
    rep = parts[11] if len(parts[11]) == 1 else "^"
    component = t[104]
    seg = t[105]
    return Delims(el, component, seg, rep)


def split_segments(text: str) -> tuple[Delims, list[list[str]]]:
    t = text.lstrip("﻿ \r\n\t")
    d = detect_delimiters(t)
    segs = []
    for raw in t.split(d.segment):
        raw = raw.strip("\r\n\t ")
        if raw:
            segs.append(raw.split(d.element))
    return d, segs


def _g(seg: list[str], i: int) -> str:
    return seg[i].strip() if len(seg) > i else ""


def _date(v: str) -> str | None:
    if not v:
        return None
    try:
        return parse_date(v[:8]).iso
    except DateError:
        return None


def _amount(v: str) -> int:
    if not v:
        return 0
    try:
        return parse_amount(v)
    except AmountError:
        return 0


def _date_range(qual: str, v: str) -> tuple[str | None, str | None]:
    if qual == "RD8" and "-" in v:
        a, b = v.split("-", 1)
        return _date(a), _date(b)
    d = _date(v)
    return d, d


@dataclass
class _Ctx:
    billing: PartyIn | None = None
    payer: PartyIn | None = None
    subscriber: PatientIn | None = None
    patient: PatientIn | None = None
    claim: InvoiceIn | None = None
    claim_rendering_npi: str | None = None
    line: LineIn | None = None
    last_nm1: str = ""
    bht_date: str | None = None
    claim_dos: tuple[str | None, str | None] = (None, None)
    claim_dx: list[str] = field(default_factory=list)
    claims: list[InvoiceIn] = field(default_factory=list)


def parse_837(segs: list[list[str]], d: Delims, *, direction: str, version: str) -> ParseResult:
    institutional = "X223" in version
    res = ParseResult(method="X12", meta={"transaction": "837I" if institutional else "837P"})
    ctx = _Ctx()

    def close_claim() -> None:
        if ctx.claim is not None:
            for li in ctx.claim.lines:
                if not li.rendering_npi:
                    li.rendering_npi = ctx.claim_rendering_npi or (ctx.billing.npi if ctx.billing else None)
                if not li.dos_from:
                    li.dos_from, li.dos_to = ctx.claim_dos
            ctx.claims.append(ctx.claim)
        ctx.claim = None
        ctx.line = None
        ctx.claim_rendering_npi = None
        ctx.claim_dos = (None, None)
        ctx.claim_dx = []

    hl_level = ""
    for pos, s in enumerate(segs):
        tag = s[0]
        try:
            if tag == "BHT":
                ctx.bht_date = _date(_g(s, 4))
            elif tag == "HL":
                close_claim()
                hl_level = _g(s, 3)
                if hl_level == "20":
                    ctx.billing, ctx.subscriber, ctx.patient, ctx.payer = None, None, None, None
                elif hl_level == "22":
                    ctx.subscriber, ctx.patient, ctx.payer = None, None, None
                elif hl_level == "23":
                    ctx.patient = None
            elif tag == "NM1":
                ent = _g(s, 1)
                ctx.last_nm1 = ent
                name = _g(s, 3)
                first = _g(s, 4)
                idq, idv = _g(s, 8), _g(s, 9)
                if ent == "85":
                    ctx.billing = PartyIn(name=name, party_type="VENDOR", npi=idv if idq == "XX" else None)
                elif ent == "IL":
                    ctx.subscriber = PatientIn(first=first, last=name, member_id=idv if idq in ("MI", "II") else None)
                elif ent == "QC":
                    ctx.patient = PatientIn(
                        first=first, last=name, member_id=ctx.subscriber.member_id if ctx.subscriber else None
                    )
                elif ent == "PR":
                    ctx.payer = PartyIn(name=name, party_type="PAYER")
                elif ent == "82":
                    npi = idv if idq == "XX" else None
                    if ctx.line is not None:
                        ctx.line.rendering_npi = npi
                    elif ctx.claim is not None:
                        ctx.claim_rendering_npi = npi
            elif tag == "N3":
                if ctx.last_nm1 == "85" and ctx.billing:
                    ctx.billing.address = " ".join(x for x in s[1:3] if x)
            elif tag == "N4":
                if ctx.last_nm1 == "85" and ctx.billing:
                    ctx.billing.address = f"{ctx.billing.address or ''} {_g(s, 1)} {_g(s, 2)} {_g(s, 3)}".strip()
                elif ctx.last_nm1 in ("IL", "QC"):
                    tgt = ctx.patient if ctx.last_nm1 == "QC" else ctx.subscriber
                    if tgt:
                        tgt.zip = _g(s, 3)
            elif tag == "REF":
                q, v = _g(s, 1), _g(s, 2)
                if q == "EI" and ctx.billing and ctx.claim is None:
                    ctx.billing.tax_id = v
                elif q == "F8" and ctx.claim is not None:
                    ctx.claim.original_invoice_ref = v
                elif q == "EA" and ctx.claim is not None:
                    tgt = ctx.patient or ctx.subscriber
                    if tgt:
                        tgt.mrn = v
            elif tag == "PER" and ctx.last_nm1 == "85" and ctx.billing:
                for i in range(3, len(s) - 1, 2):
                    if _g(s, i) == "TE":
                        ctx.billing.phone = _g(s, i + 1)
            elif tag == "DMG":
                tgt = ctx.patient if ctx.last_nm1 == "QC" else ctx.subscriber
                if tgt:
                    tgt.dob = _date(_g(s, 2))
                    tgt.sex = _g(s, 3) or None
            elif tag == "CLM":
                close_claim()
                comp = _g(s, 5).split(d.component)
                freq = comp[2] if len(comp) > 2 else None
                party = (ctx.payer if direction == "AR" else ctx.billing) or PartyIn(name="UNKNOWN")
                ctx.claim = InvoiceIn(
                    direction=direction,
                    party=PartyIn(**vars(party)),
                    invoice_number=_g(s, 1),
                    invoice_date=ctx.bht_date,
                    invoice_date_raw=ctx.bht_date,
                    total_cents=_amount(_g(s, 2)),
                    claim_type="INSTITUTIONAL" if institutional else "PROFESSIONAL",
                    claim_frequency_code=freq if freq in ("1", "7", "8") else None,
                    source_ref=f"segment {pos + 1}",
                )
                if party is ctx.billing and ctx.billing is None:
                    res.issues.append(ParseIssue(f"segment {pos + 1}", "claim without billing provider"))
            elif tag == "DTP":
                q, fmt, v = _g(s, 1), _g(s, 2), _g(s, 3)
                if q == "472" and ctx.line is not None:
                    ctx.line.dos_from, ctx.line.dos_to = _date_range(fmt, v)
                elif q in ("434", "472") and ctx.claim is not None and ctx.line is None:
                    ctx.claim_dos = _date_range(fmt, v)
            elif tag == "HI" and ctx.claim is not None:
                ctx.claim_dx.extend(e.split(d.component)[1] for e in s[1:] if d.component in e)
            elif tag == "LX" and ctx.claim is not None:
                n = _g(s, 1)
                ctx.line = LineIn(
                    line_no=int(n) if n.isdigit() else len(ctx.claim.lines) + 1,
                    patient=ctx.patient or ctx.subscriber,
                    dx_codes=list(ctx.claim_dx),
                )
                ctx.claim.lines.append(ctx.line)
            elif tag in ("SV1", "SV2") and ctx.line is not None:
                if tag == "SV1":
                    proc = _g(s, 1).split(d.component)
                    charge, units, pos_v = _g(s, 2), _g(s, 4), _g(s, 5)
                    ctx.line.place_of_service = pos_v or None
                else:
                    ctx.line.revenue_code = _g(s, 1) or None
                    proc = _g(s, 2).split(d.component)
                    charge, units = _g(s, 3), _g(s, 5)
                if len(proc) > 1:
                    ctx.line.code = proc[1]
                    ctx.line.modifiers = [m for m in proc[2:6] if m]
                    if len(proc) > 6 and proc[6]:
                        ctx.line.description = proc[6]
                elif tag == "SV2":
                    ctx.line.code = ctx.line.revenue_code
                ctx.line.charge_cents = _amount(charge)
                try:
                    ctx.line.units = float(units) if units else 1.0
                except ValueError:
                    ctx.line.units = 1.0
            elif tag == "SE":
                close_claim()
        except (IndexError, ValueError) as e:
            res.issues.append(ParseIssue(f"segment {pos + 1} ({tag})", f"could not parse: {e}"))
    close_claim()
    res.invoices = ctx.claims
    return res


def parse_835(segs: list[list[str]], d: Delims) -> ParseResult:
    res = ParseResult(method="X12", meta={"transaction": "835", "remittances": []})
    payer = None
    payee = None
    cur: dict[str, Any] | None = None
    svc: dict[str, Any] | None = None
    remits: list[dict[str, Any]] = res.meta["remittances"]
    for pos, s in enumerate(segs):
        tag = s[0]
        try:
            if tag == "N1":
                if _g(s, 1) == "PR":
                    payer = _g(s, 2)
                elif _g(s, 1) == "PE":
                    payee = _g(s, 2)
            elif tag == "CLP":
                cur = {
                    "claim_id": _g(s, 1),
                    "status_code": _g(s, 2),
                    "charge_cents": _amount(_g(s, 3)),
                    "paid_cents": _amount(_g(s, 4)),
                    "payer": payer,
                    "payee": payee,
                    "lines": [],
                }
                remits.append(cur)
                svc = None
            elif tag == "SVC" and cur is not None:
                proc = _g(s, 1).split(d.component)
                svc = {
                    "code": proc[1] if len(proc) > 1 else None,
                    "modifiers": proc[2:6],
                    "charge_cents": _amount(_g(s, 2)),
                    "paid_cents": _amount(_g(s, 3)),
                    "units": float(_g(s, 5) or 1),
                    "dos": None,
                }
                cur["lines"].append(svc)
            elif tag == "DTM" and _g(s, 1) == "472" and svc is not None:
                svc["dos"] = _date(_g(s, 2))
        except (IndexError, ValueError) as e:
            res.issues.append(ParseIssue(f"segment {pos + 1} ({tag})", f"could not parse: {e}"))
    return res


def parse_x12(text: str, *, direction: str = "AP") -> ParseResult:
    try:
        d, segs = split_segments(text)
    except X12Error as e:
        return ParseResult(method="X12", issues=[ParseIssue("envelope", str(e))])
    tx = ""
    version = ""
    for s in segs:
        if s[0] == "ST":
            tx = _g(s, 1)
            version = _g(s, 3)
            break
        if s[0] == "GS":
            version = _g(s, 8)
    if tx == "837":
        return parse_837(segs, d, direction=direction, version=version)
    if tx == "835":
        return parse_835(segs, d)
    return ParseResult(method="X12", issues=[ParseIssue("ST", f"unsupported transaction set {tx!r}")])
