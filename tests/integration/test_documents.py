"""PDF text extraction, OCR, arithmetic cross-check, correction drafts and template learning."""

from __future__ import annotations

import io
import json
import os
import shutil

import pytest

from invoice_analytics.context import Engine
from invoice_analytics.ingest.pdf.pipeline import accept_draft
from invoice_analytics.ingest.service import ingest_bytes

LINES_OK = [("Exam gloves nitrile case", "120.00"), ("Surgical masks box", "45.50"), ("Linen service", "310.25")]


def make_pdf(
    number: str,
    date: str,
    lines: list[tuple[str, str]],
    total: str,
    *,
    vendor: str = "Harbor Medical Supply",
    label: str = "Invoice No:",
    scanned: bool = False,
) -> bytes:
    text = [
        vendor,
        "Remit To: Harbor Medical Supply",
        "123 Main Street Springfield IL",
        f"{label} {number}",
        f"Invoice Date: {date}",
        "Tax ID: 12-3456789",
        "",
        "Description                                   Amount",
    ]
    text += [f"{d}                                   {a}" for d, a in lines]
    text += ["", f"Total Due: ${total}"]
    if not scanned:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        y = 740
        for t in text:
            c.drawString(60, y, t)
            y -= 18
        c.save()
        return buf.getvalue()
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (1700, 2200), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default(size=34)
    y = 120
    for t in text:
        d.text((120, y), t, fill=0, font=font)
        y += 60
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=200)
    return buf.getvalue()


def test_digital_pdf_auto_accepted(engine: Engine) -> None:
    pdf = make_pdf("HMS-20931", "03/14/2026", LINES_OK, "475.75")
    out = ingest_bytes(engine, "inv.pdf", pdf, options={"_in_process": True, "date_order": "US"})
    assert out.persist is not None, out.as_dict()["issues"]
    inv = engine.db.one("SELECT * FROM invoices")
    assert inv["invoice_number_raw"] == "HMS-20931" and inv["total_cents"] == 47575
    assert engine.db.scalar("SELECT COUNT(*) FROM invoice_lines") == 3
    assert engine.db.scalar("SELECT ingest_method FROM documents") == "PDF_TEXT"
    assert engine.db.scalar("SELECT COUNT(*) FROM document_blobs") == 1
    # same PDF re-sent under a new file name -> INV-002 + INV-001
    ingest_bytes(engine, "inv-copy.pdf", pdf, options={"_in_process": True})
    rules = {r["rule_id"] for r in engine.db.query("SELECT rule_id FROM flags")}
    assert {"INV-001", "INV-002"} <= rules


def test_arithmetic_mismatch_goes_to_review_then_learns_template(engine: Engine) -> None:
    bad = make_pdf("HMS-555", "2026-03-20", LINES_OK, "999.99", label="Inv Ref")  # unknown label, wrong total
    out = ingest_bytes(engine, "bad.pdf", bad, options={"_in_process": True})
    assert out.persist is None and out.parse.meta.get("needs_review_draft_id")
    did = out.parse.meta["needs_review_draft_id"]
    draft = json.loads(engine.db.scalar("SELECT fields FROM extraction_drafts WHERE id=?", (did,)))
    assert draft["checks"]["arithmetic"]["ok"] is False
    assert engine.db.scalar("SELECT ingest_status FROM documents") == "NEEDS_REVIEW"
    res = accept_draft(
        engine,
        did,
        {"invoice": {"invoice_number": "HMS-555", "total": "475.75", "vendor_name": "Harbor Medical Supply"}},
        user_id=None,
    )
    assert res["persist"]["invoice_count"] == 1
    tmpl = json.loads(engine.db.scalar("SELECT template FROM vendor_templates"))
    assert "Inv Ref" in tmpl["anchors"]["invoice_number"]
    # next document from the same vendor layout uses the learned anchor
    nxt = make_pdf("HMS-556", "2026-03-27", LINES_OK, "475.75", label="Inv Ref")
    out2 = ingest_bytes(engine, "next.pdf", nxt, options={"_in_process": True})
    assert out2.persist is not None
    assert engine.db.scalar("SELECT invoice_number_raw FROM invoices ORDER BY id DESC LIMIT 1") == "HMS-556"


@pytest.mark.skipif(
    shutil.which("tesseract") is None and not os.environ.get("IA_TESSERACT"), reason="tesseract not installed"
)
def test_scanned_pdf_uses_ocr(engine: Engine) -> None:
    pdf = make_pdf("HMS-77120", "03/14/2026", LINES_OK, "475.75", scanned=True)
    out = ingest_bytes(engine, "scan.pdf", pdf, options={"_in_process": True})
    assert out.parse.method == "OCR"
    if out.persist is None:
        did = out.parse.meta["needs_review_draft_id"]
        fields = json.loads(engine.db.scalar("SELECT fields FROM extraction_drafts WHERE id=?", (did,)))
        assert "HMS" in fields["text"]
    else:
        assert engine.db.scalar("SELECT ingest_method FROM documents") == "OCR"


def test_ai_extract_suggestion_for_low_confidence(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from invoice_analytics.ai.tasks.extract import extract_draft

    from ..fake_llm import FakeLLM

    llm = FakeLLM().start()
    monkeypatch.setenv("IA_LLM_BASE_URL", llm.url)
    try:
        engine.settings.set("ai.tier", "LITE")
        bad = make_pdf("HMS-9", "2026-03-20", LINES_OK, "475.75", label="Ref")
        out = ingest_bytes(engine, "bad.pdf", bad, options={"_in_process": True})
        did = out.parse.meta["needs_review_draft_id"]
        assert engine.db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='AI_EXTRACT'") == 1
        res = extract_draft(engine, did)
        assert res["status"] == "OK" and res["checks"]["arithmetic_ok"] is True
        # the AI result is a suggestion only: no invoice was created
        assert engine.db.scalar("SELECT COUNT(*) FROM invoices") == 0
    finally:
        llm.stop()
