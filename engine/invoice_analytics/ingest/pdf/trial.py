"""Clinical-trial site invoices (site -> sponsor): subject IDs, protocol visits, milestone and
pass-through items instead of CPT codes and patient names.

Layouts handled (all found in real site invoices):
  * ruled item tables spanning pages ("Subject Milestone Items" / "Pass Thru Items" sections),
    including numbers wrapped inside cells ("1234.5\\n6")
  * one table whose cells stack every row's values separated by newlines
  * no item table at all: rows are text lines "<subject> <date> <description> <qty> $ <amount>"
    with wrapped descriptions

Pure functions: pdf bytes in, plain dicts out. Runs inside the isolated extraction subprocess.
"""

from __future__ import annotations

import io
import logging
import re
from functools import partial
from typing import Any

SUBJECT_RE = re.compile(r"^(?:\d{3}-\d{3}|[A-Z]{2,5}-\d{2,4}-\d{3,5})$")
DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
AMOUNT_RE = re.compile(r"^\(?-?\$?\s*[\d,]*\d\.\d{2}\)?$")

HEADER_KEYS: dict[str, tuple[str, ...]] = {
    "subject": ("seq. no", "seq no", "screen #", "patient study id", "subject", "patient id"),
    "item": ("procedure/lab", "milestone", "visit/element", "description of service", "description", "item"),
    "visit": ("visit",),
    "date": ("occurred", "date complete", "date of service", "service date", "date"),
    "due": ("due", "item charge", "amount due", "charge"),
    "amount": ("amount",),
    "withheld": ("amount withheld", "withheld"),
    "qty": ("quantity", "qty", "units"),
    "comments": ("comments",),
}


def _clean(cell: str | None) -> str:
    return " ".join((cell or "").split())


def _number(cell: str | None) -> str | None:
    """Join numbers wrapped inside a cell ("1234.5\\n6" -> "1234.56"); strip $ and spaces."""
    if not cell:
        return None
    s = re.sub(r"\s+", "", cell).replace("$", "")
    return s.replace(",", "") if AMOUNT_RE.match(s) else None


def _nth(cols: dict[str, list[str]], j: int, k: str) -> str | None:
    """j-th non-empty value of column k in a stacked-cell table."""
    vals = [v for v in cols.get(k, []) if v]
    return vals[j] if j < len(vals) else None


def _header_map(row: list[str | None]) -> dict[str, int] | None:
    cols = [_clean(c).lower().rstrip(":") for c in row]
    found: dict[str, int] = {}
    for field in ("withheld", "subject", "item", "visit", "date", "due", "amount", "qty", "comments"):
        for i, c in enumerate(cols):
            if i in found.values() or not c:
                continue
            keys = HEADER_KEYS[field]
            if field == "visit" and c != "visit":
                continue  # "Visit/Element" is the item column, not a visit column
            if field == "amount" and c != "amount":
                continue
            if field == "due" and c not in ("due", "item charge", "amount due", "charge"):
                continue
            if any(c == k or c.startswith(k) for k in keys):
                found[field] = i
                break
    if "subject" in found and "date" in found and ("due" in found or "amount" in found):
        return found
    return None


_VISIT_TOKEN = re.compile(
    r"\b(?:Cohort\s*\d+\s*:\s*)?(D-?\d+|Day\s*-?\d+|M\d+|Month\s*\d+\s*Day\s*\d+|Screening|"
    r"Baseline(?:\s*D-?\d+)?|Leukapheresis|Lymphodepletion\s*Day\s*-?\d+)\b",
    re.I,
)


def visit_label(*texts: str | None) -> str | None:
    """Normalised protocol timepoint: "Cohort 1: D-8" -> "D-8", "Month 1 Day 5" -> "M1D5"."""
    for t in texts:
        if not t:
            continue
        m = re.search(r"Month\s*(\d+)\s*Day\s*(\d+)", t, re.I)
        if m:
            return f"M{m.group(1)}D{m.group(2)}"
        m = re.search(r"(?:Lymphodepletion\s*)?Day\s*(-?\d+)", t, re.I)
        if m:
            return f"D{m.group(1)}"
        m = re.search(r"(?<![A-Za-z])D(-?\d+)\b", t)
        if m:
            return f"D{m.group(1)}"
        m = re.search(r"(?<![A-Za-z])M(\d+)\b", t)
        if m:
            return f"M{m.group(1)}"
        m = re.search(r"\b(Screening|PreTXScrn|Baseline|Leukapheresis)", t, re.I)
        if m:
            label = m.group(1).title()
            return "Screening" if label.lower() == "pretxscrn" else label
    return None


def service_item(item: str, visit: str | None) -> str:
    """The billable service, independent of the visit it happened at.

    "repeat additional screening procedures (Abdomen Imaging:CT ABDOMEN W IV CONTRAST)"
      -> "CT ABDOMEN W IV CONTRAST";  "ScreeningW-10; CT Chest" -> "CT CHEST"."""
    s = item.strip()
    if ";" in s:
        s = s.split(";", 1)[1]
    m = re.search(r"\(([^()]*)\)\s*$", s)
    if m and ":" in m.group(1):  # "(Abdomen Imaging:CT ABDOMEN W IV CONTRAST)" names the service
        s = m.group(1).split(":")[-1]
    elif m:  # "(If required per institutional policy)" is a note
        s = s[: m.start()]
    s = re.sub(r"\s*/\s*", "/", s)  # a wrapped "Abdomen/ Pelvis" is the same service as "Abdomen/Pelvis"
    return " ".join(s.upper().split())[:80]


def _row(
    subject: str,
    item: str,
    visit: str | None,
    date: str,
    amount: str,
    qty: str | None,
    comments: str | None,
    section: str | None,
) -> dict[str, Any]:
    description = " · ".join(x for x in (item, visit if visit and visit not in item else None, comments) if x)
    return {
        "subject": subject,
        "item": service_item(item, visit),
        "visit": visit_label(visit, item),
        "description": description[:200],
        "dos": date,
        "amount": amount,
        "units": qty or "1",
        "section": section,
        "code": None,
        "modifiers": [],
    }


def _table_rows(tables: list[list[list[str | None]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    section: str | None = None
    for table in tables:
        for raw in table:
            if not raw:
                continue
            first = _clean(raw[0])
            if first in ("Subject Milestone Items", "Pass Thru Items", "Invoiceable Items", "Procedures") and not any(
                _clean(c) for c in raw[1:]
            ):
                section, header = first, None
                continue
            h = _header_map(raw)
            if h:
                header = h
                continue
            if header is None:
                continue
            cell = {k: (raw[i] if i < len(raw) else None) for k, i in header.items()}
            subj_cell = cell.get("subject") or ""
            # stacked layout: every column holds all rows separated by newlines
            parts = [p.strip() for p in subj_cell.split("\n") if p.strip()]
            if len(parts) > 1 and all(SUBJECT_RE.match(p) for p in parts):
                cols = {k: [p.strip() for p in (v or "").split("\n")] for k, v in cell.items()}
                for j, subj in enumerate(parts):
                    at = partial(_nth, cols, j)
                    amt = _number(at("due")) or _number(at("amount"))
                    d = DATE_RE.search(at("date") or "")
                    if amt and d:
                        rows.append(
                            _row(subj, at("item") or "", at("visit"), d.group(1), amt, at("qty"), None, section)
                        )
                continue
            subject = _clean(subj_cell)
            item_text = _clean(cell.get("item"))
            if not SUBJECT_RE.match(subject):
                # a subject ID that overflowed its cell: "101-00" + "3Screening" -> "101-003", "Screening"
                spill = re.match(r"^(\d+)(.*)$", item_text)
                if spill and SUBJECT_RE.match(subject + spill.group(1)):
                    subject, item_text = subject + spill.group(1), spill.group(2).strip()
                else:
                    continue
            d = DATE_RE.search(cell.get("date") or "")
            amt = _number(cell.get("due")) or _number(cell.get("amount"))
            if not (d and amt):
                continue
            rows.append(
                _row(
                    subject,
                    item_text,
                    _clean(cell.get("visit")) or None,
                    d.group(1),
                    amt,
                    _clean(cell.get("qty")) or None,
                    _clean(cell.get("comments")) or None,
                    section,
                )
            )
    return rows


_TEXT_ROW = re.compile(
    r"^(?P<subject>\d{3}-\d{3}|[A-Z]{2,5}-\d{2,4}-\d{3,5})\s+(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<desc>.+?)\s+(?P<qty>\d+)?\s*\$\s*(?P<amount>[\d,]+\.\d{2})\s*$"
)


def _text_rows(text: str) -> list[dict[str, Any]]:
    """Rows without a ruled table. A wrapped description continues on the next line(s)."""
    rows: list[dict[str, Any]] = []
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = _TEXT_ROW.match(ln)
        if not m:
            # a row whose description wraps: "<subj> <date> <desc...>" + "<rest> <qty> $ <amt>"
            head = re.match(r"^(\d{3}-\d{3}|[A-Z]{2,5}-\d{2,4}-\d{3,5})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(.+)$", ln)
            if head and i + 1 < len(lines):
                for span in (1, 2):
                    joined = f"{ln} {' '.join(lines[i + 1:i + 1 + span])}"
                    m = _TEXT_ROW.match(joined)
                    if m:
                        i += span
                        break
        if m:
            desc = m.group("desc").strip()
            visit = desc.split(";", 1)[0] if ";" in desc else None
            rows.append(
                {
                    "subject": m.group("subject"),
                    "item": service_item(desc, visit),
                    "visit": visit_label(visit, desc),
                    "description": desc[:200],
                    "dos": m.group("date"),
                    "amount": m.group("amount").replace(",", ""),
                    "units": m.group("qty") or "1",
                    "section": None,
                    "code": None,
                    "modifiers": [],
                }
            )
        i += 1
    return rows


def _column_rows(pages_words: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Rows from word positions when there is no ruled item table. Columns come from the header
    row; each subject ID anchors a row; description words (which may wrap above and below the
    anchor line because cells are vertically centred) go to the nearest anchor."""
    rows: list[dict[str, Any]] = []
    for words in pages_words:
        hdr = {w["text"].lower(): w for w in words}
        desc_w = next((w for w in words if w["text"].lower() == "description"), None)
        qty_w = next((w for w in words if w["text"].lower() in ("quantity", "qty", "units")), None)
        if desc_w is None or qty_w is None:
            continue
        head_bottom = max(w["bottom"] for w in words if abs(w["top"] - desc_w["top"]) < 12)
        anchors = sorted(
            (w for w in words if SUBJECT_RE.match(w["text"]) and w["top"] > head_bottom and w["x0"] < desc_w["x0"]),
            key=lambda w: w["top"],
        )
        if not anchors:
            continue
        stop = min(
            (w["top"] for w in words if w["text"].lower().startswith("grand") and w["top"] > anchors[-1]["top"]),
            default=anchors[-1]["bottom"] + 40,
        )
        tops = [a["top"] for a in anchors]
        bounds = [head_bottom] + [(tops[i] + tops[i + 1]) / 2 for i in range(len(tops) - 1)] + [stop]
        for i, a in enumerate(anchors):
            lo, hi = bounds[i], bounds[i + 1]
            line = sorted((w for w in words if abs(w["top"] - a["top"]) < 3), key=lambda w: w["x0"])
            date = next((w["text"] for w in line if DATE_RE.fullmatch(w["text"])), None)
            right = [w["text"] for w in line if w["x0"] >= qty_w["x0"] - 5]
            amount = next((t.replace(",", "") for t in reversed(right) if re.fullmatch(r"[\d,]+\.\d{2}", t)), None)
            qty = next((t for t in right if t.isdigit()), "1")
            # the description column starts right after the (right-aligned) date values; its header
            # word is centred, so desc_w.x0 is not the column's left edge
            desc_left = (
                max(
                    (w["x1"] for w in words if DATE_RE.fullmatch(w["text"]) and w["top"] > head_bottom),
                    default=desc_w["x0"] - 8,
                )
                + 2
            )
            desc = " ".join(
                w["text"]
                for w in sorted(
                    (w for w in words if desc_left <= w["x0"] < qty_w["x0"] - 5 and lo <= w["top"] < hi),
                    key=lambda w: (round(w["top"]), w["x0"]),
                )
            )
            if not (date and amount):
                continue
            visit = desc.split(";", 1)[0] if ";" in desc else None
            rows.append(
                {
                    "subject": a["text"],
                    "item": service_item(desc, visit),
                    "visit": visit_label(visit, desc),
                    "description": desc[:200],
                    "dos": date,
                    "amount": amount,
                    "units": qty,
                    "section": None,
                    "code": None,
                    "modifiers": [],
                }
            )
        del hdr
    return rows


def _first(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, re.I | re.M)
        if m:
            return m.group(1).strip().rstrip(".,")
    return None


def header_fields(text: str, tables: list[list[list[str | None]]]) -> dict[str, Any]:
    num = r"([A-Z0-9][A-Z0-9_\-/.#]{1,48})"
    f: dict[str, Any] = {
        "invoice_number": _first([rf"Invoice\s*(?:No\.?|Number|#)[ \t]*[:#]?[ \t]*{num}"], text),
        "invoice_date": _first([r"Invoice\s*Date[ \t]*:?[ \t]*(\d{1,2}/\d{1,2}/\d{4})"], text),
        "due_date": _first([r"Due\s*Date[ \t]*:?[ \t]*(\d{1,2}/\d{1,2}/\d{4})"], text),
        "po_number": _first([r"P\.?O\.?\s*(?:Number|No\.?|#)[ \t]*:?[ \t]*([A-Z0-9][A-Z0-9\-]{2,30})[ \t]*$"], text),
        "tax_id": _first([r"(?:Tax\s*ID|EIN|TIN|FEIN)[#:\s]*(\d{2}-?\d{7})"], text),
        "protocol": _first([r"(?:Sponsor\s*)?Protocol\s*(?:No\.?|ID)[ \t]*:?[ \t]*([A-Z0-9][A-Z0-9\-]{2,30})"], text),
    }
    total = _first(
        [r"(?:Grand\s*Total|Invoice\s*Amount|Total\s*Due|Amount\s*Due)[ \t]*:?[ \t]*\$?[ \t]*([\d,]+\.\d{2})"], text
    )
    if total is None:  # "Invoice Totals:" is a table header with the amounts in the next row
        for t in tables:
            for r_i, r in enumerate(t):
                if any("invoice totals" in _clean(c).lower() for c in r) and r_i + 1 < len(t):
                    nums = [_number(c) for c in t[r_i + 1] if _number(c)]
                    if nums:
                        total = nums[-1]
    f["total"] = total.replace(",", "") if total else None
    vendor = _first(
        [
            r"Make\s*Checks\s*Payable\s*To:[ \t]*(?:The[ \t]+)?([^.\n]+)",
            r"Remit\s*to:[ \t]*([^\n]+)",
            r"Payment\s*To:[ \t]*\n[ \t]*([^\n]+)",
        ],
        text,
    )
    if vendor:
        vendor = " ".join(vendor.replace("\n", " ").split())
    f["vendor_name"] = vendor
    return f


def _payee_block(words: list[dict[str, Any]]) -> str | None:
    """The payee name under a "Payment To:" label, read by position.

    Two-column headers ("Invoice To:" | "Payment To:") interleave in plain text, so a name that
    wraps ("... Research" / "Institute") is split by the other column's address line."""
    label = next(
        (
            w
            for i, w in enumerate(words[:-1])
            if w["text"].lower() == "payment" and words[i + 1]["text"].lower() == "to:"
        ),
        None,
    )
    if label is None:
        return None
    mid = (label["top"] + label["bottom"]) / 2
    below = [w for w in words if w["top"] > mid and w["x0"] >= label["x0"] - 2]
    out: list[str] = []
    last_top = label["top"]
    for top in sorted({round(w["top"]) for w in below}):
        line = " ".join(w["text"] for w in sorted(below, key=lambda w: w["x0"]) if round(w["top"]) == top)
        if top - last_top > 30 or not line or line[0].isdigit() or len(out) == 3:
            break  # the address starts with a street number
        out.append(line)
        last_top = top
    return " ".join(out) or None


def extract_trial(data: bytes) -> dict[str, Any] | None:
    """Returns {fields, lines} when the document looks like a site invoice with subject rows."""
    import pdfplumber

    tables: list[list[list[str | None]]] = []
    texts: list[str] = []
    words: list[list[dict[str, Any]]] = []
    with pdfplumber.open(io.BytesIO(data)) as doc:
        for page in doc.pages[:60]:
            texts.append(page.extract_text() or "")
            words.append(page.extract_words(keep_blank_chars=False, use_text_flow=False))
            try:
                tables.extend(page.extract_tables())
            except Exception as e:  # noqa: BLE001 - a malformed page must not stop extraction
                logging.getLogger("invoice_analytics.ingest.trial").warning(
                    "table extraction failed on a page: %s", type(e).__name__
                )
                continue
    text = "\n".join(texts)
    payee = _payee_block(words[0]) if words else None
    rows = _table_rows(tables) or _column_rows(words) or _text_rows(text)
    if not rows:
        return None
    fields = header_fields(text, tables)
    if payee and fields["vendor_name"] and payee.startswith(fields["vendor_name"]):
        fields["vendor_name"] = payee
    fields["lines"] = rows
    fields["document_kind"] = "clinical_trial_site_invoice"
    conf = {
        "invoice_number": 0.95 if fields["invoice_number"] else 0.0,
        "invoice_date": 0.95 if fields["invoice_date"] else 0.0,
        "total": 0.9 if fields["total"] else 0.0,
        "vendor_name": (
            0.95 if fields["vendor_name"] and fields.get("tax_id") else (0.9 if fields["vendor_name"] else 0.0)
        ),
    }
    fields["confidence"] = conf
    return fields
