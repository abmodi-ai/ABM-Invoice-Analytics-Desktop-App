"""Ingest entry point: detect the file type, parse, persist, then run incremental detection."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.ingest.csv import CsvOptions, header_signature, parse_table, read_table, suggest_mapping
from invoice_analytics.ingest.csv.importer import MalformedFile
from invoice_analytics.ingest.model import ParseIssue, ParseResult
from invoice_analytics.ingest.persist import PersistResult, persist
from invoice_analytics.ingest.x12 import parse_x12
from invoice_analytics.normalize import normalize_invoice_number
from invoice_analytics.security import audit

log = logging.getLogger("invoice_analytics.ingest")

MAX_FILE_BYTES = 200 * 1024 * 1024
CSV_EXT = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")
X12_EXT = (".x12", ".edi", ".837", ".835", ".dat")
PDF_EXT = (".pdf",)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


class NeedsMapping(Exception):
    """A CSV/Excel file has no saved template; the UI must show the mapping wizard."""

    def __init__(self, headers: list[str], suggestion: dict[str, Any], preview: list[list[str]]) -> None:
        super().__init__("column mapping required")
        self.headers = headers
        self.suggestion = suggestion
        self.preview = preview


@dataclass
class IngestOutcome:
    persist: PersistResult | None
    parse: ParseResult
    detection: dict[str, Any] | None
    remittances_applied: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.parse.method,
            "confidence": self.parse.confidence,
            "issues": [i.as_dict() for i in self.parse.issues[:200]],
            "persist": self.persist.as_dict() if self.persist else None,
            "detection": self.detection,
            "remittances_applied": self.remittances_applied,
            "seconds": round(self.seconds, 3),
            "meta": {k: v for k, v in self.parse.meta.items() if k not in ("remittances", "draft_fields")},
        }


def sniff_kind(filename: str, data: bytes) -> str:
    name = filename.lower()
    head = data[:512].lstrip(b"\xef\xbb\xbf \r\n\t")
    if head.startswith(b"ISA"):
        return "X12"
    if head.startswith(b"%PDF") or name.endswith(PDF_EXT):
        return "PDF"
    if name.endswith(IMAGE_EXT) or head[:4] in (b"\x89PNG", b"II*\x00", b"MM\x00*") or head[:3] == b"\xff\xd8\xff":
        return "IMAGE"
    if name.endswith(X12_EXT):
        return "X12"
    if head[:2] == b"PK" and name.endswith((".xlsx", ".xlsm")):
        return "CSV"
    if name.endswith(CSV_EXT):
        return "CSV"
    return "UNKNOWN"


def find_template(eng: Engine, headers: list[str]) -> dict[str, Any] | None:
    sig = header_signature(headers)
    row = eng.db.one("SELECT * FROM csv_mapping_templates WHERE header_sig=? ORDER BY updated_at DESC LIMIT 1", (sig,))
    return row


def save_template(
    eng: Engine,
    name: str,
    headers: list[str] | None,
    mapping: dict[str, str],
    options: dict[str, Any],
    user_id: int | None,
) -> int:
    sig = header_signature(headers) if headers else None
    with eng.db.tx() as c:
        cur = c.execute(
            "INSERT INTO csv_mapping_templates(name, header_sig, mapping, options, created_by) VALUES (?,?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET header_sig=excluded.header_sig, mapping=excluded.mapping,"
            " options=excluded.options, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (name, sig, json.dumps(mapping), json.dumps(options), user_id),
        )
        tid = int(
            cur.lastrowid or c.execute("SELECT id FROM csv_mapping_templates WHERE name=?", (name,)).fetchone()["id"]
        )
        audit.record(
            eng.db,
            "CSV_TEMPLATE_SAVE",
            user_id=user_id,
            entity_type="csv_template",
            entity_id=tid,
            after={"name": name, "mapping": mapping},
            conn=c,
        )
    return tid


def ingest_bytes(
    eng: Engine,
    filename: str,
    data: bytes,
    *,
    user_id: int | None = None,
    mapping: dict[str, str] | None = None,
    template_id: int | None = None,
    options: dict[str, Any] | None = None,
    detect: bool = True,
) -> IngestOutcome:
    t0 = time.perf_counter()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    options = dict(options or {})
    sha = hashlib.sha256(data).hexdigest()
    kind = sniff_kind(filename, data)
    safe_name = Path(filename).name  # never trust client-supplied directories
    if kind == "CSV":
        try:
            table = read_table(data, safe_name, options.get("sheet"))
        except MalformedFile as e:
            return IngestOutcome(
                None, ParseResult(method="CSV", issues=[ParseIssue("file", str(e))]), None, 0, time.perf_counter() - t0
            )
        tmpl = None
        if template_id is not None:
            tmpl = eng.db.one("SELECT * FROM csv_mapping_templates WHERE id=?", (template_id,))
        elif mapping is None:
            tmpl = find_template(eng, table.headers)
        if tmpl is not None:
            mapping = json.loads(tmpl["mapping"])
            options = {**json.loads(tmpl["options"] or "{}"), **options}
        if mapping is None:
            raise NeedsMapping(table.headers, suggest_mapping(table.headers), table.rows[:5])
        pr = parse_table(table, mapping, CsvOptions.from_dict(options))
    elif kind == "X12":
        pr = parse_x12(data.decode("latin-1"), direction=options.get("direction", "AP"))
    elif kind in ("PDF", "IMAGE"):
        from invoice_analytics.ingest.pdf.pipeline import extract_document

        pr = extract_document(eng, data, safe_name, kind=kind, options=options)
    else:
        pr = ParseResult(method="CSV", issues=[ParseIssue("file", f"unsupported file type: {safe_name}")])
    remits = pr.meta.get("remittances") or []
    applied = apply_remittances(eng, remits, user_id) if remits else 0
    if not pr.invoices and not remits:
        if pr.meta.get("document_id") and kind in ("PDF", "IMAGE"):
            store_blob(eng, int(pr.meta["document_id"]), data)
        return IngestOutcome(None, pr, None, 0, time.perf_counter() - t0)
    pres = persist(eng, pr, sha256=sha, source_path=safe_name, mime=kind, user_id=user_id)
    det = None
    if kind in ("PDF", "IMAGE"):
        store_blob(eng, pres.document_id, data)
    if pr.meta.get("draft_fields"):
        with eng.db.tx() as c:
            c.execute(
                "INSERT INTO extraction_drafts(document_id, method, fields, status) VALUES (?,?,?,'ACCEPTED')",
                (pres.document_id, pr.method, json.dumps(pr.meta["draft_fields"])),
            )
    if detect and pres.invoice_ids:
        det = run_incremental(eng, pres, user_id)
    return IngestOutcome(pres, pr, det, applied, time.perf_counter() - t0)


def run_incremental(eng: Engine, pres: PersistResult, user_id: int | None) -> dict[str, Any]:
    from invoice_analytics.linkage.patients import quick_link_new_patients
    from invoice_analytics.scoring.pipeline import detect

    if pres.new_patient_ids:
        quick_link_new_patients(eng, pres.new_patient_ids)
    eng.store.upsert_invoices(pres.invoice_ids)
    if pres.new_patient_ids:
        eng.store.refresh_clusters()
    res = detect(eng, pres.invoice_ids, user_id=user_id)
    return res.stats()


def apply_remittances(eng: Engine, remits: list[dict[str, Any]], user_id: int | None) -> int:
    """835: mark matching claims PAID and record line-level paid amounts."""
    n = 0
    touched: list[int] = []
    with eng.db.tx() as c:
        for r in remits:
            num = normalize_invoice_number(r.get("claim_id"))
            if not num:
                continue
            invs = c.execute(
                "SELECT id FROM invoices WHERE invoice_number_norm=? AND deleted_at IS NULL", (num,)
            ).fetchall()
            for inv in invs:
                status = "PAID" if r.get("paid_cents", 0) > 0 else None
                if r.get("status_code") == "22":  # reversal of previous payment
                    status = "OPEN"
                if status:
                    c.execute(
                        "UPDATE invoices SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                        (status, inv["id"]),
                    )
                for li in r.get("lines", []):
                    c.execute(
                        "UPDATE invoice_lines SET paid_cents=? WHERE invoice_id=? AND code=?"
                        " AND (? IS NULL OR dos_from=?)",
                        (li.get("paid_cents"), inv["id"], li.get("code"), li.get("dos"), li.get("dos")),
                    )
                touched.append(inv["id"])
                n += 1
        audit.record(
            eng.db,
            "REMITTANCE_APPLY",
            user_id=user_id,
            entity_type="invoice",
            after={"claims": len(remits), "matched": n},
            conn=c,
        )
    if touched:
        eng.store.upsert_invoices(touched)
    return n


def store_blob(eng: Engine, document_id: int, data: bytes) -> None:
    with eng.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO document_blobs(document_id, data) VALUES (?,?)", (document_id, data))
