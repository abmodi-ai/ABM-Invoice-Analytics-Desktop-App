"""PDF / image text extraction and heuristic field extraction.

Runs inside a separate process with a timeout (see pipeline.py) so a malicious or pathological
PDF cannot hang or crash the engine. Pure functions only: input bytes -> plain dicts.
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any

MIN_TEXT_CHARS = 40
MAX_PAGES = 50

_NUM = r"([A-Z0-9][A-Z0-9\-/.#]{1,24})"
_AMT = r"\$?\s*\(?-?[\d,]*\d\.\d{2}\)?-?"
_DATE = r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})"
PATTERNS = {
    "invoice_number": [
        rf"(?:INVOICE|INV|BILL|STATEMENT)\s*(?:NO\.?|NUMBER|NUM|#)\s*[:#]?\s*{_NUM}",
        rf"\bINVOICE\s*[:#]\s*{_NUM}",
    ],
    "invoice_date": [rf"(?:INVOICE|BILL|STATEMENT)?\s*DATE\s*[:]?\s*{_DATE}"],
    "due_date": [rf"DUE\s*DATE\s*[:]?\s*{_DATE}"],
    "total": [rf"(?:TOTAL\s*(?:DUE|AMOUNT|AMOUNT DUE)?|AMOUNT\s*DUE|BALANCE\s*DUE)\s*[:]?\s*({_AMT})"],
    "po_number": [rf"(?:P\.?O\.?|PURCHASE ORDER)\s*(?:NO\.?|NUMBER|#)?\s*[:#]?\s*{_NUM}"],
    "tax_id": [r"(?:TAX\s*ID|EIN|TIN|FEIN)\s*[:#]?\s*(\d{2}-?\d{7})"],
    "npi": [r"\bNPI\s*[:#]?\s*(\d{10})\b"],
}
LINE_RE = re.compile(
    r"^(?P<desc>.*?)\s*(?P<code>\b\d{4}[0-9A-Z]\b|\b[A-V]\d{4}\b)?\s*(?:(?P<mods>(?:\b[0-9A-Z]{2}\b[ ,]?){1,4}))?\s*"
    r"(?P<units>\b\d{1,3}(?:\.\d+)?\b)?\s+(?P<amount>" + _AMT + r")\s*$",
    re.IGNORECASE,
)
DOS_RE = re.compile(_DATE)


def _clean_amount(s: str) -> str:
    return s.replace("$", "").replace(" ", "")


def page_texts(data: bytes, kind: str) -> tuple[list[dict[str, Any]], int]:
    """Returns ([{page, text, method, confidence}], page_count). OCR is used where no text layer exists."""
    pages: list[dict[str, Any]] = []
    if kind == "IMAGE":
        from PIL import Image

        from invoice_analytics.ingest.ocr.tesseract import ocr_png, words_to_lines

        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert("L").save(buf, format="PNG")
        lines = words_to_lines(ocr_png(buf.getvalue()))
        text = "\n".join(t for t, _ in lines)
        conf = sum(c for _, c in lines) / len(lines) if lines else 0.0
        return [{"page": 1, "text": text, "method": "OCR", "confidence": conf}], 1
    import pdfplumber
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    n = len(pdf)
    with pdfplumber.open(io.BytesIO(data)) as doc:
        for i, page in enumerate(doc.pages[:MAX_PAGES]):
            text = page.extract_text(layout=False) or ""
            if len(text.strip()) >= MIN_TEXT_CHARS:
                pages.append({"page": i + 1, "text": text, "method": "PDF_TEXT", "confidence": 0.99})
                continue
            from invoice_analytics.ingest.ocr.tesseract import ocr_png, words_to_lines

            bitmap = pdf[i].render(scale=300 / 72, grayscale=True)
            img = bitmap.to_pil()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            del img, bitmap
            lines = words_to_lines(ocr_png(buf.getvalue()))
            buf.seek(0)
            buf.truncate(0)  # wipe the in-memory image
            pages.append(
                {
                    "page": i + 1,
                    "text": "\n".join(t for t, _ in lines),
                    "method": "OCR",
                    "confidence": sum(c for _, c in lines) / len(lines) if lines else 0.0,
                }
            )
    pdf.close()
    return pages, n


def fingerprint(text: str) -> str:
    """Layout fingerprint of a vendor's documents: the first alphabetic tokens of page 1."""
    toks = [t for t in re.findall(r"[A-Za-z]{3,}", text[:600].upper())][:12]
    return hashlib.sha256(" ".join(toks).encode()).hexdigest()[:24]


def _first(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".,")
    return None


def heuristic_fields(text: str, template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract header fields + lines. Template anchors (learned from corrections) take precedence."""
    fields: dict[str, Any] = {}
    conf: dict[str, float] = {}
    for f, pats in PATTERNS.items():
        tpats = (template or {}).get("anchors", {}).get(f)
        if tpats:
            v = _first([rf"{re.escape(a)}\s*[:#]?\s*(\S+(?: \d{{1,2}},? \d{{4}})?)" for a in tpats], text)
            if v:
                fields[f], conf[f] = v, 0.97
                continue
        v = _first(pats, text)
        if v:
            fields[f], conf[f] = v, 0.85
    # vendor: template value, a "Remit to"/"From" line, else the first non-empty line
    vendor = (template or {}).get("vendor_name")
    if not vendor:
        m = re.search(r"(?:REMIT\s*TO|FROM|BILL\s*FROM)\s*[:]?\s*(.+)", text, re.IGNORECASE)
        letterhead = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        vendor = m.group(1).strip() if m else letterhead
        # corroboration: remit-to name equals the letterhead, or a tax ID identifies the party
        agree = bool(m) and vendor.upper() == letterhead.upper()
        conf["vendor_name"] = 0.9 if agree else (0.6 if m else 0.4)
        if fields.get("tax_id") or fields.get("npi"):
            conf["vendor_name"] = max(conf["vendor_name"], 0.9)
    else:
        conf["vendor_name"] = 0.97
    fields["vendor_name"] = vendor[:120]
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or re.search(r"\b(TOTAL|SUBTOTAL|BALANCE|AMOUNT DUE|TAX)\b", s, re.IGNORECASE):
            continue
        m = LINE_RE.match(s)
        if not m or not m.group("amount"):
            continue
        desc = (m.group("desc") or "").strip()
        dos = DOS_RE.search(desc)
        if dos:
            desc = desc.replace(dos.group(0), "").strip()
        if len(desc) < 2 and not m.group("code"):
            continue
        lines.append(
            {
                "description": desc[:200],
                "code": (m.group("code") or None),
                "modifiers": (m.group("mods") or "").replace(",", " ").split(),
                "units": m.group("units") or "1",
                "amount": _clean_amount(m.group("amount")),
                "dos": dos.group(0) if dos else None,
            }
        )
    fields["lines"] = lines
    fields["confidence"] = conf
    return fields


def extract(data: bytes, kind: str, templates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pages, n = page_texts(data, kind)
    text = "\n".join(p["text"] for p in pages)
    fp = fingerprint(pages[0]["text"] if pages else "")
    tmpl = templates.get(fp)
    fields = None
    if kind == "PDF" and all(p["method"] == "PDF_TEXT" for p in pages):
        from invoice_analytics.ingest.pdf.trial import extract_trial

        fields = extract_trial(data)  # clinical-trial site invoice tables (subject / visit / item rows)
    if fields is None:
        fields = heuristic_fields(text, tmpl)
    method = "OCR" if any(p["method"] == "OCR" for p in pages) else "PDF_TEXT"
    page_conf = min((p["confidence"] for p in pages), default=0.0)
    return {
        "pages": pages,
        "page_count": n,
        "text": text,
        "fingerprint": fp,
        "template_used": bool(tmpl),
        "fields": fields,
        "method": method,
        "page_confidence": page_conf,
    }
