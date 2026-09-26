"""Validate, normalize and persist parsed invoices. Resolves parties and patients deterministically.

Parties: same party_type + exact tax ID HMAC or NPI -> same party (deterministic merge).
         Otherwise exact normalized name or NAME alias. Fuzzy party matches are only suggested
         later by linkage/, never applied here.
Patients: exact patient_key (HMAC of normalized first|last|dob) -> same patient. Everything
          fuzzier is left to linkage/ (Splink), which sets cluster_id.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.ingest.model import LineIn, ParseResult, PartyIn, PatientIn
from invoice_analytics.normalize import (
    classify_code,
    normalize_address,
    normalize_code,
    normalize_description,
    normalize_invoice_number,
    normalize_modifiers,
    normalize_npi,
    normalize_party_name,
    normalize_person_name,
    normalize_phone,
    normalize_tax_id,
    normalize_zip,
    ocr_fold,
)
from invoice_analytics.normalize.embeddings import get_embedder, to_blob
from invoice_analytics.security import audit

log = logging.getLogger("invoice_analytics.ingest")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class PersistResult:
    document_id: int
    invoice_ids: list[int] = field(default_factory=list)
    line_count: int = 0
    new_patient_ids: list[int] = field(default_factory=list)
    new_party_ids: list[int] = field(default_factory=list)
    duplicate_file_of: list[int] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "invoice_ids": self.invoice_ids,
            "invoice_count": len(self.invoice_ids),
            "line_count": self.line_count,
            "new_patients": len(self.new_patient_ids),
            "new_parties": len(self.new_party_ids),
            "duplicate_file_of": self.duplicate_file_of,
            "issues": self.issues,
        }


class Resolver:
    """Per-ingest caches for party and patient lookups (keeps bulk imports fast)."""

    def __init__(self, eng: Engine, conn: Any) -> None:
        self.eng = eng
        self.c = conn
        self.party_cache: dict[tuple[str, ...], int] = {}
        self.patient_cache: dict[str, int] = {}
        self.new_parties: list[int] = []
        self.new_patients: list[int] = []

    # ------------------------------------------------------------ parties
    def party(self, p: PartyIn) -> int:
        ptype = p.party_type if p.party_type in ("VENDOR", "CUSTOMER", "PAYER") else "VENDOR"
        name_norm = normalize_party_name(p.name) or "UNKNOWN"
        tax = normalize_tax_id(p.tax_id)
        tax_h = self.eng.hmac(tax, "tax_id") if tax else None
        npi = normalize_npi(p.npi)
        addr = normalize_address(p.address)
        phone = normalize_phone(p.phone)
        ck = (ptype, tax_h or "", npi or "", name_norm)
        if ck in self.party_cache:
            return self.party_cache[ck]
        pid = None
        if tax_h:
            r = self.c.execute(
                "SELECT p.id FROM parties p WHERE p.party_type=? AND p.tax_id_hmac=? UNION "
                "SELECT a.party_id FROM party_aliases a JOIN parties p ON p.id=a.party_id "
                "WHERE p.party_type=? AND a.alias_type='TAX_ID' AND a.value_norm=? LIMIT 1",
                (ptype, tax_h, ptype, tax_h),
            ).fetchone()
            pid = r["id"] if r else None
        if pid is None and npi:
            r = self.c.execute("SELECT id FROM parties WHERE party_type=? AND npi=? LIMIT 1", (ptype, npi)).fetchone()
            pid = r["id"] if r else None
        if pid is None:
            # Exact name match only when identifiers do not contradict.
            r = self.c.execute(
                "SELECT p.id, p.tax_id_hmac, p.npi, p.remit_address_norm FROM parties p WHERE p.party_type=? AND p.name_norm=? "
                "UNION SELECT p.id, p.tax_id_hmac, p.npi, p.remit_address_norm FROM party_aliases a JOIN parties p ON p.id=a.party_id "
                "WHERE p.party_type=? AND a.alias_type='NAME' AND a.value_norm=?",
                (ptype, name_norm, ptype, name_norm),
            ).fetchall()
            for row in r:
                if tax_h and row["tax_id_hmac"] and row["tax_id_hmac"] != tax_h:
                    continue
                if npi and row["npi"] and row["npi"] != npi:
                    continue
                # A name alone is weak evidence: with no tax ID / NPI on either side, a different
                # remit address means a different entity until an admin merges them.
                no_ids = not (tax_h or npi) and not (row["tax_id_hmac"] or row["npi"])
                if no_ids and addr and row["remit_address_norm"] and row["remit_address_norm"] != addr:
                    continue
                pid = row["id"]
                break
        if pid is None:
            cur = self.c.execute(
                "INSERT INTO parties(party_type, display_name, name_norm, tax_id_hmac, npi,"
                " remit_address_norm, phone_norm) VALUES (?,?,?,?,?,?,?)",
                (ptype, p.name.strip() or name_norm, name_norm, tax_h, npi, addr, phone),
            )
            pid = int(cur.lastrowid)
            self.c.execute("UPDATE parties SET cluster_id=? WHERE id=?", (pid, pid))
            self.new_parties.append(pid)
        else:
            existing = self.c.execute("SELECT * FROM parties WHERE id=?", (pid,)).fetchone()
            if existing["name_norm"] != name_norm:
                self._alias(pid, "NAME", name_norm)
            if tax_h and not existing["tax_id_hmac"]:
                self.c.execute("UPDATE parties SET tax_id_hmac=? WHERE id=?", (tax_h, pid))
            elif tax_h and existing["tax_id_hmac"] != tax_h:
                self._alias(pid, "TAX_ID", tax_h)
            if npi and not existing["npi"]:
                self.c.execute("UPDATE parties SET npi=? WHERE id=?", (npi, pid))
            elif npi and existing["npi"] != npi:
                self._alias(pid, "NPI", npi)
            if addr and not existing["remit_address_norm"]:
                self.c.execute("UPDATE parties SET remit_address_norm=? WHERE id=?", (addr, pid))
            elif addr and existing["remit_address_norm"] != addr:
                self._alias(pid, "ADDRESS", addr)
            if phone and not existing["phone_norm"]:
                self.c.execute("UPDATE parties SET phone_norm=? WHERE id=?", (phone, pid))
        self.party_cache[ck] = pid
        return pid

    def _alias(self, pid: int, t: str, v: str) -> None:
        self.c.execute(
            "INSERT OR IGNORE INTO party_aliases(party_id, alias_type, value_norm, source) VALUES (?,?,?,?)",
            (pid, t, v, "ingest"),
        )

    # ------------------------------------------------------------ patients
    def patient(self, p: PatientIn | None, source_party_id: int) -> int | None:
        if p is None or p.is_empty():
            return None
        n = normalize_person_name(p.first, p.last)
        dob = p.dob or ""
        if n.last:
            key = self.eng.hmac(f"{n.first}|{n.last}|{dob}", "patient_key")
        elif p.subject_id:
            # study subject IDs are unique per site (the billing party), not globally
            key = self.eng.hmac(f"subject|{source_party_id}|{p.subject_id.strip().upper()}", "patient_key")
        else:
            ident = p.member_id or p.mrn or p.account or ""
            key = self.eng.hmac(f"id-only|{source_party_id}|{ident}", "patient_key")
        if key in self.patient_cache:
            pid = self.patient_cache[key]
        else:
            r = self.c.execute("SELECT id FROM patients WHERE patient_key=?", (key,)).fetchone()
            if r:
                pid = r["id"]
            else:
                fc = self.eng.field_cipher
                sex = (p.sex or "").strip().upper()[:1] or None
                cur = self.c.execute(
                    "INSERT INTO patients(patient_key, first_name_enc, last_name_enc,"
                    " first_phonetic_hmac, last_phonetic_hmac, dob, sex, zip5, subject_id_enc)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        key,
                        fc.encrypt(n.first, aad="patient.first"),
                        fc.encrypt(n.last, aad="patient.last"),
                        self.eng.hmac(n.first_phonetic, "phonetic") if n.first_phonetic else None,
                        self.eng.hmac(n.last_phonetic, "phonetic") if n.last_phonetic else None,
                        p.dob,
                        sex if sex in ("M", "F", "U", "X") else None,
                        normalize_zip(p.zip),
                        fc.encrypt(p.subject_id.strip().upper(), aad="patient.subject") if p.subject_id else None,
                    ),
                )
                pid = int(cur.lastrowid)
                self.c.execute("UPDATE patients SET cluster_id=? WHERE id=?", (pid, pid))
                self.new_patients.append(pid)
            self.patient_cache[key] = pid
        for id_type, val in (("MEMBER_ID", p.member_id), ("MRN", p.mrn), ("ACCOUNT", p.account)):
            if val and val.strip():
                self.c.execute(
                    "INSERT OR IGNORE INTO patient_source_ids(patient_id, source_party_id, id_type, value_hmac)"
                    " VALUES (?,?,?,?)",
                    (pid, source_party_id, id_type, self.eng.hmac(val.strip().upper(), f"source_id:{id_type}")),
                )
        return pid


def _line_row(li: LineIn, invoice_id: int, patient_id: int | None) -> tuple[Any, ...]:
    code = normalize_code(li.code)
    mods = normalize_modifiers(li.modifiers)
    raw_mods = [m for m in (li.modifiers or []) if m][:4] + [None] * 4
    desc_norm = normalize_description(li.description)
    return (
        invoice_id,
        li.line_no,
        patient_id,
        li.dos_from,
        li.dos_to or li.dos_from,
        classify_code(code) if code else None,
        code,
        json.dumps(mods),
        *[(m.strip().upper() if m else None) for m in raw_mods[:4]],
        float(li.units or 0),
        int(li.charge_cents),
        li.allowed_cents,
        li.paid_cents,
        normalize_npi(li.rendering_npi) or (li.rendering_npi or None),
        li.place_of_service,
        li.revenue_code,
        json.dumps(sorted({d.strip().upper() for d in li.dx_codes if d})),
        li.description,
        desc_norm,
        (li.visit or None),
    )


def persist(
    eng: Engine,
    result: ParseResult,
    *,
    sha256: str,
    source_path: str | None,
    mime: str | None,
    user_id: int | None,
    document_id: int | None = None,
) -> PersistResult:
    """Persist one document and its invoices in a single transaction.

    `document_id` attaches invoices to an existing document (accepted PDF extraction drafts)."""
    embedder = get_embedder(eng.config.models_dir)
    ts = _now()
    with eng.db.tx() as c:
        prior_docs = [
            r["id"]
            for r in c.execute("SELECT id FROM documents WHERE sha256=? AND deleted_at IS NULL", (sha256,)).fetchall()
        ]
        status = "OK" if not result.errors else ("NEEDS_REVIEW" if result.invoices else "FAILED")
        if document_id is not None:
            prior_docs = [d for d in prior_docs if d != document_id]
            c.execute(
                "UPDATE documents SET ingest_status=?, ingest_method=?, ingest_confidence=?, ingested_at=?,"
                " ingested_by=? WHERE id=?",
                (status, result.method, result.confidence, ts, user_id, document_id),
            )
            doc_id = document_id
        else:
            cur = c.execute(
                "INSERT INTO documents(source_path, sha256, mime, page_count, ingest_method, ingest_status,"
                " ingest_confidence, ingest_error, ingested_at, ingested_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    source_path,
                    sha256,
                    mime,
                    result.page_count,
                    result.method,
                    status,
                    result.confidence,
                    json.dumps([i.as_dict() for i in result.errors][:50]) if result.errors else None,
                    ts,
                    user_id,
                ),
            )
            doc_id = int(cur.lastrowid)
        res = PersistResult(
            document_id=doc_id, duplicate_file_of=prior_docs, issues=[i.as_dict() for i in result.issues[:200]]
        )
        rz = Resolver(eng, c)
        line_rows: list[tuple[Any, ...]] = []
        descs: list[str] = []
        for inv in result.invoices:
            party_id = rz.party(inv.party)
            total = inv.effective_total()
            num_norm = normalize_invoice_number(inv.invoice_number)
            is_credit = 1 if (total < 0 or inv.status == "CREDIT") else 0
            status_v = inv.status if inv.status in ("OPEN", "PAID", "VOID", "CREDIT") else "OPEN"
            freq = inv.claim_frequency_code if inv.claim_frequency_code in ("1", "7", "8") else None
            cur = c.execute(
                "INSERT INTO invoices(document_id, direction, party_id, invoice_number_raw,"
                " invoice_number_norm, invoice_number_ocr, invoice_date, invoice_date_raw, due_date,"
                " total_cents, currency, po_number, claim_type, claim_frequency_code,"
                " original_invoice_ref, status, is_credit, needs_attention)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    inv.direction,
                    party_id,
                    inv.invoice_number,
                    num_norm,
                    ocr_fold(num_norm),
                    inv.invoice_date,
                    inv.invoice_date_raw,
                    inv.due_date,
                    total,
                    inv.currency,
                    inv.po_number,
                    inv.claim_type,
                    freq,
                    normalize_invoice_number(inv.original_invoice_ref),
                    status_v,
                    is_credit,
                    "; ".join(inv.needs_attention) or None,
                ),
            )
            inv_id = int(cur.lastrowid)
            res.invoice_ids.append(inv_id)
            for li in inv.lines:
                pid = rz.patient(li.patient, party_id)
                row = _line_row(li, inv_id, pid)
                line_rows.append(row)
                descs.append(row[-2])
        if line_rows:
            vecs = embedder.embed(descs)
            rows = [(*r, to_blob(vecs[i]) if descs[i] else None) for i, r in enumerate(line_rows)]
            c.executemany(
                "INSERT INTO invoice_lines(invoice_id, line_no, patient_id, dos_from, dos_to, code_system,"
                " code, modifiers, modifier_1, modifier_2, modifier_3, modifier_4, units, charge_cents,"
                " allowed_cents, paid_cents, rendering_npi, place_of_service, revenue_code, dx_codes,"
                " description_raw, description_norm, visit_label, description_embedding)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        res.line_count = len(line_rows)
        res.new_party_ids = rz.new_parties
        res.new_patient_ids = rz.new_patients
        apply_claim_links(c, res.invoice_ids)
        audit.record(
            eng.db,
            "INGEST",
            user_id=user_id,
            entity_type="document",
            entity_id=doc_id,
            after={
                "method": result.method,
                "invoices": len(res.invoice_ids),
                "lines": res.line_count,
                "status": status,
                "sha256": sha256,
                "embedder": embedder.embedder_id,
            },
            conn=c,
        )
    return res


def apply_claim_links(c: Any, invoice_ids: list[int] | None = None) -> int:
    """Frequency code 7 (replacement) and 8 (void) link to the original claim they reference.

    SUP-002/SUP-003 rely on these links. Code 8 also marks the original VOID.
    """
    where = ""
    params: list[Any] = []
    if invoice_ids is not None:
        if not invoice_ids:
            return 0
        where = f" AND n.id IN ({','.join('?' * len(invoice_ids))})"
        params = list(invoice_ids)
    rows = c.execute(
        "SELECT n.id AS new_id, n.claim_frequency_code AS freq, o.id AS orig_id FROM invoices n"
        " JOIN parties pn ON pn.id=n.party_id"
        " JOIN invoices o ON o.invoice_number_norm=n.original_invoice_ref AND o.id<>n.id"
        " JOIN parties po ON po.id=o.party_id AND po.cluster_id=pn.cluster_id"
        " WHERE n.claim_frequency_code IN ('7','8') AND n.original_invoice_ref IS NOT NULL"
        " AND n.deleted_at IS NULL" + where,
        params,
    ).fetchall()
    for r in rows:
        link = "REPLACES" if r["freq"] == "7" else "VOIDS"
        c.execute(
            "INSERT OR IGNORE INTO invoice_links(from_invoice_id, to_invoice_id, link_type) VALUES (?,?,?)",
            (r["new_id"], r["orig_id"], link),
        )
        if link == "VOIDS":
            c.execute("UPDATE invoices SET status='VOID', updated_at=? WHERE id=?", (_now(), r["orig_id"]))
    return len(rows)
