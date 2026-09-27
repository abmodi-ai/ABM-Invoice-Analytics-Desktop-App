"""Clinical-trial site invoices (site -> sponsor): extraction and detection on synthetic PDFs that
mimic real site layouts. Real client invoices are never committed."""

from __future__ import annotations

import io
import json
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from invoice_analytics.context import Engine
from invoice_analytics.ingest.pdf.trial import extract_trial, service_item, visit_label
from invoice_analytics.ingest.service import ingest_bytes

GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8)])


def ruled_site_invoice(number: str, date: str, milestones: list[tuple], passthru: list[tuple]) -> bytes:
    """UW-style: header tables, 'Subject Milestone Items' and 'Pass Thru Items' ruled tables,
    'Invoice Totals:' table. Rows: (subject, item, visit|None, date, amount)."""
    st = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    story: list = [
        Table([["Clinical Trial Invoice", ""], [f"Invoice No.: {number}", f"Invoice Date: {date}"]], style=GRID),
        Table(
            [
                ["Remit To:"],
                ["Example University Cancer Center\n600 Main St\nSpringfield IL 62701"],
                ["Make Checks Payable To: Example University Research Board. Tax ID#: 39-1234567"],
            ],
            style=GRID,
        ),
        Paragraph("Sponsor: Example Therapeutics   Sponsor Protocol No: ABC-101", st["Normal"]),
        Spacer(1, 8),
    ]
    total = Decimal(0)
    if milestones:
        rows = [
            ["Subject Milestone Items", "", "", "", "", "", ""],
            ["Seq. No.", "Milestone", "Occurred", "Amount", "Amount\nWithheld", "Due", "Comments"],
        ]
        for subj, item, _visit, d, amt in milestones:
            rows.append([subj, item, d, amt, "", amt, ""])
            total += Decimal(amt)
        story += [Table(rows, style=GRID), Spacer(1, 8)]
    if passthru:
        story.append(PageBreak())
        rows = [
            ["Pass Thru Items", "", "", "", "", "", "", ""],
            ["Seq. No.", "Procedure/Lab", "Visit", "Occurred:", "Amount", "Amount\nWithheld", "Due", "Comments"],
        ]
        for subj, item, visit, d, amt in passthru:
            rows.append([subj, item, visit or "", d, amt, "", amt, ""])
            total += Decimal(amt)
        story += [Table(rows, style=GRID), Spacer(1, 8)]
    story.append(
        Table([["Invoice Totals:", "Amount", "", "Due"], ["", f"{total:,.2f}", "", f"{total:,.2f}"]], style=GRID)
    )
    doc.build(story)
    return buf.getvalue()


def stacked_site_invoice(number: str, rows: list[tuple]) -> bytes:
    """Sarah-Cannon-style: one table whose cells stack all rows' values separated by newlines."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    total = sum(Decimal(r[3]) for r in rows)
    st = getSampleStyleSheet()
    table = [
        ["Screen #", "Random #", "Visit/Element", "Protocol Version", "Date\nComplete", "Item Charge"],
        [
            "\n".join(r[0] for r in rows),
            "\n".join(r[0] for r in rows),
            "\n".join(r[1] for r in rows),
            "\n".join("PROTO-301" for _ in rows),
            "\n".join(r[2] for r in rows),
            "\n".join(f"${Decimal(r[3]):,.2f}" for r in rows),
        ],
    ]
    doc.build(
        [
            Paragraph(f"Invoice Number: {number}", st["Normal"]),
            Paragraph("Invoice Date: 09/22/2023", st["Normal"]),
            Paragraph(f"Invoice Amount: ${total:,.2f}", st["Normal"]),
            Paragraph("Remit to: Example Research Institute, LLC", st["Normal"]),
            Spacer(1, 8),
            Table(table, style=GRID),
        ]
    )
    return buf.getvalue()


def borderless_site_invoice(number: str, rows: list[tuple]) -> bytes:
    """KU-style: no ruled item table; long descriptions wrap onto a second line; a two-column
    "Invoice To: | Payment To:" header where the payee name wraps."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    y = 740
    for t in ("Invoice Date: 09/05/2023", f"Invoice Number: {number}", "Tax ID: 48-7654321"):
        c.drawString(60, y, t)
        y -= 16
    for left, right in (
        ("Invoice To:", "Payment To:"),
        ("", "Example Medical Center Research"),
        ("100 Sponsor Way", ""),
        ("", "Institute"),
        ("Springfield, IL 62701", "200 Research Drive"),
    ):
        c.drawString(60, y, left)
        c.drawString(320, y, right)
        y -= 8
    y -= 20
    for x, t in (
        (60, "Patient Study Id"),
        (160, "Date of"),
        (260, "Description of Service"),
        (430, "Quantity"),
        (490, "Amount Due"),
    ):
        c.drawString(x, y, t)
    c.drawString(160, y - 11, "Service")
    y -= 36
    total = Decimal(0)
    for subj, d, desc, amt in rows:
        first, rest = (desc[:26], desc[26:]) if len(desc) > 26 else (desc, "")
        c.drawString(60, y, subj)
        c.drawString(160, y, d)
        c.drawString(250, y, first)
        c.drawString(440, y, "1")
        c.drawString(490, y, f"$ {Decimal(amt):,.2f}")
        if rest:
            c.drawString(250, y - 12, rest)
            y -= 12
        y -= 22
        total += Decimal(amt)
    c.drawString(60, y - 6, "Grand Total:")
    c.drawString(490, y - 6, f"$ {total:,.2f}")
    c.save()
    return buf.getvalue()


def _ingest(eng: Engine, name: str, pdf: bytes) -> dict:
    out = ingest_bytes(eng, name, pdf, options={"_in_process": True}).as_dict()
    assert out["persist"] is not None, out["issues"]
    return out


def _flags(eng: Engine) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in eng.db.query("SELECT rule_id, tier, evidence FROM flags WHERE active=1 AND suppressed_by IS NULL"):
        out.setdefault(f["rule_id"], []).append(json.loads(f["evidence"]))
    return out


MILESTONES = [
    ("101-001", "Screening", None, "08/20/2022", "6500.00"),
    ("101-001", "Cohort 1: D-1", None, "12/03/2022", "1980.00"),
    ("101-001", "Cohort 1: D0", None, "12/04/2022", "16750.00"),
    ("101-002", "Cohort 2: D-1", None, "02/25/2023", "1980.00"),
    ("101-002", "Cohort 2: D0", None, "02/25/2023", "16750.00"),
]  # D-1 and D0 on one date
PASSTHRU = [
    ("101-006", "Travel Reimbursement", "Cohort 5: D-8", "07/28/2023", "82.50"),
    ("101-006", "Travel Reimbursement", "Cohort 5: D-7", "07/28/2023", "82.50"),  # same trip twice
    ("101-006", "Travel Reimbursement", "Cohort 5: D-6", "07/30/2023", "82.50"),
    ("101-009", "CT CHEST W CONTRAST", "Screening", "07/29/2023", "4120.00"),
    ("101-009", "CT PELVIS W IV CONTRAST", "Screening", "07/29/2023", "3899.00"),
]
REPEAT = [
    (
        "101-009",
        "repeat additional screening procedures (Chest Imaging:CT CHEST W CONTRAST)",
        None,
        "08/19/2023",
        "4120.00",
    ),
    (
        "101-009",
        "repeat additional screening procedures (Pelvic Imaging:CT PELVIS W IV CONTRAST)",
        None,
        "08/19/2023",
        "3899.00",
    ),
]


def test_labels() -> None:
    assert visit_label("Cohort 1: D-8") == "D-8"
    assert visit_label("Month 1 Day 5") == "M1D5"
    assert visit_label("Lymphodepletion Day -5") == "D-5"
    assert visit_label("ScreeningW-10") == "Screening"
    assert visit_label("M2") == "M2"
    assert (
        service_item("repeat additional screening procedures (Abdomen Imaging:CT ABDOMEN W IV CONTRAST)", None)
        == "CT ABDOMEN W IV CONTRAST"
    )
    assert (
        service_item("PreTXScrn_LDBslnD-8; Covid-19 PCR test (If required per institutional policy)", None)
        == "COVID-19 PCR TEST"
    )


def test_ruled_tables_extract_and_reconcile() -> None:
    f = extract_trial(ruled_site_invoice("EX_001_Milestones", "04/07/2023", MILESTONES, PASSTHRU))
    assert f is not None and f["invoice_number"] == "EX_001_Milestones" and f["invoice_date"] == "04/07/2023"
    assert f["tax_id"] == "39-1234567" and f["vendor_name"].startswith("Example University")
    assert len(f["lines"]) == 10
    assert sum(Decimal(li["amount"]) for li in f["lines"]) == Decimal(f["total"])
    travel = [li for li in f["lines"] if li["item"] == "TRAVEL REIMBURSEMENT"]
    assert [li["visit"] for li in travel] == ["D-8", "D-7", "D-6"]


def test_stacked_and_borderless_layouts() -> None:
    rows = [
        ("XYZ-201-1003", "Lymphodepletion Day -5", "07/30/2023", "990.00"),
        ("XYZ-201-1003", "Month 1 Day 1", "08/05/2023", "1500.00"),
        ("XYZ-201-1003", "Day 0", "08/04/2023", "4100.00"),
    ]
    f = extract_trial(stacked_site_invoice("500123", rows))
    assert f and len(f["lines"]) == 3 and [li["visit"] for li in f["lines"]] == ["D-5", "M1D1", "D0"]
    assert sum(Decimal(li["amount"]) for li in f["lines"]) == Decimal(f["total"])
    krows = [
        ("204-010", "07/01/2023", "ScreeningW-10; CT Chest", "2980.10"),
        ("204-010", "07/01/2023", "ScreeningW-10; CT Abdomen/Pelvis", "4650.40"),
        ("204-007", "07/02/2023", "PreTXScrn_LDBslnD-8; Liver Biopsy", "5123.00"),
    ]
    k = extract_trial(borderless_site_invoice("STUDY001-12345", krows))
    assert k and [li["item"] for li in k["lines"]] == ["CT CHEST", "CT ABDOMEN/PELVIS", "LIVER BIOPSY"]
    assert sum(Decimal(li["amount"]) for li in k["lines"]) == Decimal(k["total"])
    assert k["vendor_name"] == "Example Medical Center Research Institute"


def test_detects_site_invoice_anomalies(engine: Engine) -> None:
    _ingest(engine, "milestones.pdf", ruled_site_invoice("EX_006_Milestones", "04/07/2023", MILESTONES, PASSTHRU))
    _ingest(engine, "passthru.pdf", ruled_site_invoice("EX_015", "10/02/2023", REPEAT, []))
    fl = _flags(engine)
    # the same travel reimbursement twice on one invoice, billed as two visits on one date
    assert len(fl.get("CLN-010", [])) == 1 and "D-8" in fl["CLN-010"][0]["summary"]
    # two different timepoints on one date: 101-006 D-8/D-7 and 101-002 D-1/D0
    visits = sorted(e["summary"].split(" for ")[0] for e in fl.get("CLN-012", []))
    assert visits == ["Visits D-1 and D0", "Visits D-7 and D-8"], visits
    # the repeated high-cost imaging 21 days later (chest + pelvis)
    assert len(fl.get("CLN-011", [])) == 2 and all("21 days" in e["summary"] for e in fl["CLN-011"])
    # subjects are shown to reviewers as subject IDs
    detail = engine.db.one("SELECT subject_id_enc FROM patients LIMIT 1")
    assert engine.field_cipher.decrypt(detail["subject_id_enc"], aad="patient.subject").startswith("101-")


def test_clean_site_invoice_raises_nothing(engine: Engine) -> None:
    clean = [
        ("101-001", "Screening", None, "08/20/2022", "6500.00"),
        ("101-001", "Cohort 1: D-8", None, "11/26/2022", "3100.00"),
        ("101-001", "Cohort 1: D-7", None, "11/27/2022", "950.00"),
        ("101-002", "Screening", None, "09/03/2022", "6500.00"),
    ]
    trips = [
        ("101-006", "Travel Reimbursement", "Cohort 5: D-8", "07/28/2023", "82.50"),
        ("101-006", "Travel Reimbursement", "Cohort 5: D-7", "07/29/2023", "82.50"),
        ("101-006", "CT CHEST W CONTRAST", "Screening", "07/01/2023", "4120.00"),
        ("101-006", "CT CHEST W CONTRAST", "M2", "09/03/2023", "4120.00"),
    ]  # 64 days later: scheduled rescan
    _ingest(engine, "clean.pdf", ruled_site_invoice("EX_020", "10/02/2023", clean, trips))
    strong = engine.db.query(
        "SELECT rule_id FROM flags WHERE active=1 AND suppressed_by IS NULL AND tier IN ('HARD','PROBABLE')"
    )
    assert strong == []


FIRST_VISITS = [
    ("XYZ-201-1003", "Lymphodepletion Day -5", "07/30/2023", "990.00"),
    ("XYZ-201-1003", "Month 1 Day 1", "08/05/2023", "1500.00"),
    ("XYZ-201-1003", "Month 1 Day 2", "08/06/2023", "1900.00"),
]


def test_new_invoice_rebilling_an_earlier_visit_is_flagged(engine: Engine) -> None:
    first = _ingest(engine, "first.pdf", stacked_site_invoice("500123", FIRST_VISITS))
    assert first["detection"]["history"]["earlier_lines"] == 0  # nothing earlier for this subject
    later_rows = [
        ("XYZ-201-1003", "Month 1 Day 1", "08/12/2023", "1500.00"),  # already billed on 500123 (dated 08/05)
        ("XYZ-201-1003", "Month 2 Day 1", "09/02/2023", "2600.00"),
    ]
    out = _ingest(engine, "second.pdf", stacked_site_invoice("500200", later_rows))
    h = out["detection"]["history"]
    assert h["lines"] == 2 and h["patients"] == 1
    assert h["earlier_lines"] == 3 and h["earlier_invoices"] == 1
    # the visit repeat leads; CLN-011 (same $1,500 charge within 30 days) flags the same pair
    assert [r["rule_id"] for r in h["repeats"]] == ["CLN-013", "CLN-011"]
    assert "M1D1" in h["repeats"][0]["summary"] and "500123" in h["repeats"][0]["summary"]


def test_new_invoice_with_only_new_visits_reports_nothing_billed_before(engine: Engine) -> None:
    _ingest(engine, "first.pdf", stacked_site_invoice("500123", FIRST_VISITS))
    rows = [
        ("XYZ-201-1003", "Month 1 Day 28", "09/01/2023", "2568.00"),
        ("XYZ-201-1003", "Month 2 Day 7", "09/08/2023", "2385.00"),
    ]
    h = _ingest(engine, "next.pdf", stacked_site_invoice("500300", rows))["detection"]["history"]
    assert h["repeats"] == [] and h["earlier_lines"] == 3 and h["patients"] == 1
