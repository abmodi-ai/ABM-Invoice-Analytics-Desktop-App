"""Render a synthetic World into ingestable documents: CSV batches, X12 837P claims and 835 remits."""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from dataclasses import dataclass

from synthgen.generator import Invoice, Line, Vendor, World

CSV_HEADER = [
    "Vendor",
    "Tax ID",
    "Vendor NPI",
    "Remit Address",
    "Invoice Number",
    "Invoice Date",
    "Invoice Total",
    "Status",
    "Frequency Code",
    "Original Claim",
    "Line",
    "Patient First",
    "Patient Last",
    "DOB",
    "Sex",
    "Zip",
    "Member ID",
    "DOS",
    "CPT",
    "Modifiers",
    "Units",
    "Charge",
    "Rendering NPI",
    "Description",
]
CSV_MAPPING = {
    "party_name": "Vendor",
    "party_tax_id": "Tax ID",
    "party_npi": "Vendor NPI",
    "party_address": "Remit Address",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "invoice_total": "Invoice Total",
    "status": "Status",
    "claim_frequency_code": "Frequency Code",
    "original_invoice_ref": "Original Claim",
    "line_no": "Line",
    "patient_first": "Patient First",
    "patient_last": "Patient Last",
    "patient_dob": "DOB",
    "patient_sex": "Sex",
    "patient_zip": "Zip",
    "member_id": "Member ID",
    "dos_from": "DOS",
    "code": "CPT",
    "modifiers": "Modifiers",
    "units": "Units",
    "charge": "Charge",
    "rendering_npi": "Rendering NPI",
    "description": "Description",
}
PAYER = "Blue Prairie Health Plan"


@dataclass
class Document:
    name: str
    data: bytes
    kind: str  # CSV | X12
    date: str
    ingest_twice: bool = False


def _money(c: int) -> str:
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}{c // 100}.{c % 100:02d}"


def _patient_fields(li: Line) -> tuple[str, str, str, str, str, str]:
    p = li.patient
    if p is None:
        return ("", "", "", "", "", "")
    o = li.patient_override or {}
    return (o.get("first", p.first), o.get("last", p.last), o.get("dob", p.dob), p.sex, p.zip, p.member_id)


def _csv_rows(inv: Invoice) -> list[list[str]]:
    v = inv.vendor
    name = inv.party_name_override or v.name
    tax = "" if inv.no_tax_id else v.tax_id
    npi = "" if inv.no_tax_id else v.npi
    rows = []
    for n, li in enumerate(inv.lines, start=1):
        first, last, dob, sex, zp, mid = _patient_fields(li)
        units = str(int(li.units)) if float(li.units).is_integer() else str(li.units)
        rows.append(
            [
                name,
                tax,
                npi,
                inv.party_address_override or v.address,
                inv.number,
                inv.date,
                _money(inv.amount),
                inv.status,
                inv.freq or "",
                inv.orig_ref or "",
                str(n),
                first,
                last,
                dob,
                sex,
                zp,
                mid,
                li.dos,
                li.code or "",
                ",".join(li.mods),
                units,
                _money(li.charge),
                li.npi or "",
                li.desc,
            ]
        )
    return rows


def to_csv(invoices: list[Invoice]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for inv in invoices:
        w.writerows(_csv_rows(inv))
    return buf.getvalue().encode()


def _isa(ctrl: int, date: dt.date) -> str:
    f = [
        "00",
        " " * 10,
        "00",
        " " * 10,
        "ZZ",
        "IASYNTH".ljust(15),
        "ZZ",
        "RECEIVER".ljust(15),
        date.strftime("%y%m%d"),
        "1200",
        "^",
        "00501",
        f"{ctrl:09d}",
        "0",
        "T",
        ":",
    ]
    return "ISA*" + "*".join(f) + "~"


def to_837(v: Vendor, claims: list[Invoice], ctrl: int) -> bytes:
    d0 = dt.date.fromisoformat(claims[0].date)
    segs = [_isa(ctrl, d0), f"GS*HC*IASYNTH*RECEIVER*{d0:%Y%m%d}*1200*{ctrl}*X*005010X222A1"]
    for n, inv in enumerate(claims, start=1):
        st = [
            f"ST*837*{n:04d}*005010X222A1",
            f"BHT*0019*00*{inv.number}*{dt.date.fromisoformat(inv.date):%Y%m%d}*1200*CH",
            "NM1*41*2*IA SYNTH*****46*SYNTH",
            "PER*IC*SUPPORT*TE*2175550100",
            f"NM1*40*2*{PAYER.upper()}*****46*99999",
            "HL*1**20*1",
            f"NM1*85*2*{v.name.upper()}*****XX*{v.npi}",
            f"N3*{v.address.split(',')[0].upper()}",
            "N4*SPRINGFIELD*IL*62701",
            f"REF*EI*{v.tax_id.replace('-', '')}",
        ]
        p = inv.lines[0].patient
        assert p is not None
        first, last, dob, sex, zp, mid = _patient_fields(inv.lines[0])
        st += [
            "HL*2*1*22*0",
            "SBR*P*18*******MC",
            f"NM1*IL*1*{last.upper()}*{first.upper()}****MI*{mid}",
            "N3*1 PATIENT WAY",
            f"N4*SPRINGFIELD*IL*{zp}",
            f"DMG*D8*{dob.replace('-', '')}*{sex}",
            f"NM1*PR*2*{PAYER.upper()}*****PI*99999",
        ]
        freq = inv.freq or "1"
        st.append(f"CLM*{inv.number}*{_money(inv.amount)}***11:B:{freq}*Y*A*Y*Y")
        if inv.orig_ref and freq in ("7", "8"):
            st.append(f"REF*F8*{inv.orig_ref}")
        st.append("HI*ABK:M545")
        for ln, li in enumerate(inv.lines, start=1):
            proc = ":".join(["HC", li.code or ""] + li.mods[:4])
            units = str(int(li.units)) if float(li.units).is_integer() else str(li.units)
            st += [
                f"LX*{ln}",
                f"SV1*{proc}*{_money(li.charge)}*UN*{units}*11**1",
                f"DTP*472*D8*{li.dos.replace('-', '')}",
                f"NM1*82*1*PROVIDER*RENDERING****XX*{li.npi}",
            ]
        st.append(f"SE*{len(st) + 1}*{n:04d}")
        segs += st
    segs += [f"GE*{len(claims)}*{ctrl}", f"IEA*1*{ctrl:09d}"]
    return ("~".join(s.rstrip("~") for s in segs) + "~").encode()


def to_835(v: Vendor, claims: list[Invoice], ctrl: int) -> bytes:
    d0 = dt.date.fromisoformat(claims[-1].date) + dt.timedelta(days=21)
    total = sum(i.amount for i in claims)
    segs = [
        _isa(ctrl, d0),
        f"GS*HP*PAYER*IASYNTH*{d0:%Y%m%d}*1200*{ctrl}*X*005010X221A1",
        "ST*835*0001",
        f"BPR*I*{_money(int(total * 0.8))}*C*ACH*CCP*01*999999999*DA*123456*1512345678**01*999988880*DA*98765*{d0:%Y%m%d}",
        f"TRN*1*{ctrl}*1512345678",
        f"DTM*405*{d0:%Y%m%d}",
        f"N1*PR*{PAYER.upper()}",
        f"N1*PE*{v.name.upper()}*XX*{v.npi}",
    ]
    for n, inv in enumerate(claims, start=1):
        paid = int(inv.amount * 0.8)
        first, last, _dob, _sex, _zp, mid = _patient_fields(inv.lines[0])
        segs += [
            f"LX*{n}",
            f"CLP*{inv.number}*1*{_money(inv.amount)}*{_money(paid)}**MC*PCN{ctrl}{n}",
            f"NM1*QC*1*{last.upper()}*{first.upper()}****MI*{mid}",
        ]
        for li in inv.lines:
            units = str(int(li.units)) if float(li.units).is_integer() else str(li.units)
            segs += [
                f"SVC*HC:{li.code}*{_money(li.charge)}*{_money(int(li.charge * 0.8))}**{units}",
                f"DTM*472*{li.dos.replace('-', '')}",
            ]
    segs += [f"SE*{len(segs) - 1}*0001", f"GE*1*{ctrl}", f"IEA*1*{ctrl:09d}"]
    return ("~".join(segs) + "~").encode()


def render(world: World) -> list[Document]:
    """Monthly batch files per vendor, in chronological order."""
    docs: list[Document] = []
    batches: dict[tuple[str, str], list[Invoice]] = defaultdict(list)
    sft = set(world.same_file_twice)
    twice: list[Invoice] = []
    for inv in world.invoices:
        if inv.key() in sft:
            twice.append(inv)
            continue
        batches[(inv.date[:7], inv.vendor.code)].append(inv)
    ctrl = 1
    for (month, vcode), invs in sorted(batches.items()):
        v = invs[0].vendor
        last_date = max(i.date for i in invs)
        if v.fmt == "X12" and all(i.lines and i.lines[0].patient for i in invs):
            docs.append(Document(f"{vcode}_{month}.837", to_837(v, invs, ctrl), "X12", last_date))
            ctrl += 1
            paid = [i for i in invs if i.status == "PAID"]
            if paid:
                docs.append(
                    Document(
                        f"{vcode}_{month}_remit.835",
                        to_835(v, paid, ctrl),
                        "X12",
                        (dt.date.fromisoformat(last_date) + dt.timedelta(days=21)).isoformat(),
                    )
                )
                ctrl += 1
        else:
            docs.append(Document(f"{vcode}_{month}.csv", to_csv(invs), "CSV", last_date))
    for inv in twice:
        data = to_csv([inv])
        docs.append(Document(f"{inv.vendor.code}_resend_{inv.number}.csv", data, "CSV", inv.date, ingest_twice=True))
    docs.sort(key=lambda d: (d.date, d.name))
    return docs
