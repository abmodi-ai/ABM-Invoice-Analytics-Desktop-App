"""Permanently delete invoices (one, several, or all of them for a fresh start).

Unlike retention (a soft delete), this removes the rows: the invoice, its lines, the source
document and its stored PDF, every flag that involves the invoice (as subject or counterpart),
their reviews and AI output, and patients no remaining line refers to. Users, settings, reference
data, import templates and the audit log are kept; the audit log records the deletion (counts only,
no PHI). The connection runs with secure_delete, so freed pages are zeroed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from invoice_analytics.security import audit

if TYPE_CHECKING:
    from invoice_analytics.context import Engine


def _in(ids: list[int]) -> str:
    return ",".join("?" * len(ids))


def _chunks(ids: list[int], n: int = 500) -> list[list[int]]:
    return [ids[i : i + n] for i in range(0, len(ids), n)]


def delete_invoices(eng: Engine, invoice_ids: list[int] | None, *, user_id: int | None) -> dict[str, Any]:
    """Delete the given invoices, or every invoice and document when `invoice_ids` is None."""
    everything = invoice_ids is None
    counts: dict[str, int] = {}
    with eng.db.tx() as c:
        if everything:
            ids = [r["id"] for r in c.execute("SELECT id FROM invoices").fetchall()]
        else:
            ids = sorted(set(invoice_ids or []))
            if ids:
                found = {r["id"] for r in c.execute(f"SELECT id FROM invoices WHERE id IN ({_in(ids)})", ids)}
                missing = sorted(set(ids) - found)
                if missing:
                    raise KeyError(f"invoice not found: {missing[0]}")
        wanted = set(ids)
        flag_ids = [
            r["id"]
            for r in c.execute(
                "SELECT id, subject_invoice_id AS s, counterpart_invoice_ids AS cp FROM flags"
            ).fetchall()
            if everything or r["s"] in wanted or _touches(r["cp"], wanted)
        ]
        doc_ids = sorted(
            {
                r["document_id"]
                for ch in _chunks(ids)
                for r in c.execute(
                    f"SELECT document_id FROM invoices WHERE id IN ({_in(ch)}) AND document_id IS NOT NULL", ch
                )
            }
        )
        n = 0
        for ch in _chunks(flag_ids):
            c.execute(f"DELETE FROM reviews WHERE flag_id IN ({_in(ch)})", ch)
            c.execute(f"DELETE FROM ai_suggestions WHERE target_type='FLAG' AND target_id IN ({_in(ch)})", ch)
            c.execute(
                f"DELETE FROM jobs WHERE status IN ('QUEUED','FAILED','CANCELLED','DONE')"
                f" AND json_extract(payload,'$.flag_id') IN ({_in(ch)})",
                ch,
            )
            n += c.execute(f"DELETE FROM flags WHERE id IN ({_in(ch)})", ch).rowcount
        counts["flags"] = n
        n = 0
        for ch in _chunks(ids):
            c.execute(
                f"DELETE FROM invoice_links WHERE from_invoice_id IN ({_in(ch)}) OR to_invoice_id IN ({_in(ch)})",
                [*ch, *ch],
            )
            c.execute(f"DELETE FROM invoice_lines WHERE invoice_id IN ({_in(ch)})", ch)
            n += c.execute(f"DELETE FROM invoices WHERE id IN ({_in(ch)})", ch).rowcount
        counts["invoices"] = n
        if everything:  # documents still waiting for correction have no invoice yet
            doc_ids = [r["id"] for r in c.execute("SELECT id FROM documents").fetchall()]
        n = 0
        for ch in _chunks(doc_ids):
            keep = {
                r["document_id"]
                for r in c.execute(f"SELECT document_id FROM invoices WHERE document_id IN ({_in(ch)})", ch)
            }
            gone = [d for d in ch if d not in keep]  # a CSV/X12 file can hold other invoices
            if gone:
                c.execute(f"DELETE FROM extraction_drafts WHERE document_id IN ({_in(gone)})", gone)
                c.execute(f"DELETE FROM document_blobs WHERE document_id IN ({_in(gone)})", gone)
                n += c.execute(f"DELETE FROM documents WHERE id IN ({_in(gone)})", gone).rowcount
        counts["documents"] = n
        orphans = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM patients WHERE id NOT IN"
                " (SELECT patient_id FROM invoice_lines WHERE patient_id IS NOT NULL)"
            ).fetchall()
        ]
        for ch in _chunks(orphans):
            c.execute(f"DELETE FROM patient_source_ids WHERE patient_id IN ({_in(ch)})", ch)
            c.execute(
                f"DELETE FROM identity_suggestions WHERE entity_type='PATIENT'"
                f" AND (left_id IN ({_in(ch)}) OR right_id IN ({_in(ch)}))",
                [*ch, *ch],
            )
            c.execute(f"DELETE FROM patients WHERE id IN ({_in(ch)})", ch)
        counts["patients"] = len(orphans)
        if everything:  # vendors with no invoices left, unless a learned PDF layout still points at them
            parties = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM parties WHERE id NOT IN (SELECT party_id FROM invoices)"
                    " AND id NOT IN (SELECT party_id FROM vendor_templates WHERE party_id IS NOT NULL)"
                    " AND id NOT IN (SELECT source_party_id FROM patient_source_ids WHERE source_party_id IS NOT NULL)"
                ).fetchall()
            ]
            for ch in _chunks(parties):
                c.execute(f"DELETE FROM party_aliases WHERE party_id IN ({_in(ch)})", ch)
                c.execute(
                    f"DELETE FROM identity_suggestions WHERE entity_type='PARTY'"
                    f" AND (left_id IN ({_in(ch)}) OR right_id IN ({_in(ch)}))",
                    [*ch, *ch],
                )
                c.execute(f"DELETE FROM parties WHERE id IN ({_in(ch)})", ch)
            counts["parties"] = len(parties)
        audit.record(
            eng.db,
            "DATA_DELETE_ALL" if everything else "INVOICE_DELETE",
            user_id=user_id,
            entity_type="invoice",
            entity_id=None if everything else ",".join(map(str, ids)),
            after=counts,
            conn=c,
        )
    store = eng._store
    if store is not None and store.loaded:  # otherwise it loads fresh on the next detection run
        store.load_all()
    return counts


def _touches(counterparts_json: str | None, wanted: set[int]) -> bool:
    import json

    try:
        return any(int(x) in wanted for x in json.loads(counterparts_json or "[]"))
    except (TypeError, ValueError):
        return False
