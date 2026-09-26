from __future__ import annotations

import json

import pytest

from verismo_engine.context import Engine
from verismo_engine.ingest.service import NeedsMapping, ingest_bytes
from verismo_engine.scoring.pipeline import detect

HEADER = (
    "Vendor,Tax ID,Invoice Number,Invoice Date,Total,Line,Patient First,Patient Last,DOB,"
    "Member ID,DOS,CPT,Modifiers,Units,Charge,Rendering NPI,Description,Frequency Code,Original Claim\n"
)
MAPPING = {
    "party_name": "Vendor",
    "party_tax_id": "Tax ID",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "invoice_total": "Total",
    "line_no": "Line",
    "patient_first": "Patient First",
    "patient_last": "Patient Last",
    "patient_dob": "DOB",
    "member_id": "Member ID",
    "dos_from": "DOS",
    "code": "CPT",
    "modifiers": "Modifiers",
    "units": "Units",
    "charge": "Charge",
    "rendering_npi": "Rendering NPI",
    "description": "Description",
    "claim_frequency_code": "Frequency Code",
    "original_invoice_ref": "Original Claim",
}
NPI = "1234567893"


def row(
    vendor: str,
    num: str,
    date: str,
    total: str,
    line: int,
    first: str,
    last: str,
    dos: str,
    cpt: str,
    mods: str = "",
    units: str = "1",
    charge: str = "100.00",
    freq: str = "",
    orig: str = "",
    desc: str = "therapeutic exercise",
) -> str:
    return (
        f"{vendor},12-3456789,{num},{date},{total},{line},{first},{last},1980-05-17,M123,{dos},{cpt},"
        f"{mods},{units},{charge},{NPI},{desc},{freq},{orig}\n"
    )


def ingest(eng: Engine, name: str, body: str) -> dict:
    return ingest_bytes(
        eng, name, (HEADER + body).encode(), mapping=MAPPING, options={"direction": "AP", "strict_dates": True}
    ).as_dict()


def rules_fired(eng: Engine, active_only: bool = True) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    q = "SELECT * FROM flags" + (" WHERE active=1 AND suppressed_by IS NULL" if active_only else "")
    for f in eng.db.query(q):
        out.setdefault(f["rule_id"], []).append(f)
    return out


def test_needs_mapping_for_unknown_csv(engine: Engine) -> None:
    with pytest.raises(NeedsMapping) as ei:
        ingest_bytes(
            engine,
            "x.csv",
            (HEADER + row("Acme", "1", "2026-03-10", "100", 1, "A", "B", "2026-03-04", "97110")).encode(),
        )
    assert ei.value.suggestion["invoice_number"]["column"] == "Invoice Number"
    assert ei.value.suggestion["dos_from"]["column"] == "DOS"


def test_exact_resubmission_flags_inv001_and_cln001(engine: Engine) -> None:
    ingest(
        engine,
        "a.csv",
        row("Acme Therapy LLC", "INV-0045", "2026-03-10", "100.00", 1, "Maria", "Garcia", "2026-03-04", "97110"),
    )
    out = ingest(
        engine,
        "b.csv",
        row("ACME THERAPY", "INV 45", "2026-04-02", "100.00", 1, "Maria", "Garcia", "2026-03-04", "97110"),
    )
    assert out["detection"]["new_flags"] >= 2
    fired = rules_fired(engine)
    assert "INV-001" in fired and fired["INV-001"][0]["tier"] == "HARD"
    assert "CLN-001" in fired
    ev = json.loads(fired["CLN-001"][0]["evidence"])
    assert {m["field"] for m in ev["matched_fields"]} >= {"patient_cluster", "dos", "code", "units"}
    assert ev["amount_at_risk_cents"] == 10000
    assert "97110" in ev["summary"]


def test_same_file_twice_is_inv002(engine: Engine) -> None:
    body = row("Acme", "77", "2026-03-10", "250.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="250.00")
    ingest(engine, "a.csv", body)
    ingest(engine, "a-copy.csv", body)
    assert "INV-002" in rules_fired(engine)


def test_same_total_same_date_new_number_is_inv003(engine: Engine) -> None:
    ingest(
        engine,
        "a.csv",
        row("Acme", "100", "2026-03-10", "480.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="480.00"),
    )
    ingest(
        engine,
        "b.csv",
        row("Acme", "200", "2026-03-10", "480.00", 1, "Bob", "Ray", "2026-03-02", "99214", charge="480.00"),
    )
    fired = rules_fired(engine)
    assert "INV-003" in fired and fired["INV-003"][0]["tier"] == "PROBABLE"


def test_idempotent_rerun(engine: Engine) -> None:
    ingest(engine, "a.csv", row("Acme", "45", "2026-03-10", "100.00", 1, "Maria", "Garcia", "2026-03-04", "97110"))
    ingest(engine, "b.csv", row("Acme", "45", "2026-04-02", "100.00", 1, "Maria", "Garcia", "2026-03-04", "97110"))
    n1 = engine.db.scalar("SELECT COUNT(*) FROM flags")
    r = detect(engine)
    r2 = detect(engine)
    assert engine.db.scalar("SELECT COUNT(*) FROM flags") == n1
    assert not r.new_flag_ids and not r2.new_flag_ids


def test_credit_memo_suppresses(engine: Engine) -> None:
    ingest(
        engine,
        "a.csv",
        row("Acme", "500", "2026-03-10", "300.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="300.00"),
    )
    ingest(
        engine,
        "b.csv",
        row("Acme", "500-CR", "2026-03-15", "-300.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="-300.00"),
    )
    ingest(
        engine,
        "c.csv",
        row("Acme", "501", "2026-03-20", "300.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="300.00"),
    )
    flags = engine.db.query("SELECT rule_id, suppressed_by FROM flags WHERE rule_id='CLN-001'")
    assert flags and all(f["suppressed_by"] == "SUP-001" for f in flags)


def test_replacement_claim_suppressed_and_void_marks_original(engine: Engine) -> None:
    ingest(
        engine, "a.csv", row("Acme", "C100", "2026-03-10", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213", freq="1")
    )
    ingest(
        engine,
        "b.csv",
        row("Acme", "C101", "2026-03-20", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213", freq="7", orig="C100"),
    )
    fl = engine.db.query("SELECT rule_id, suppressed_by FROM flags WHERE rule_id IN ('CLN-001','CLN-009')")
    assert fl and all(f["suppressed_by"] == "SUP-002" for f in fl)
    assert engine.db.scalar("SELECT COUNT(*) FROM invoice_links WHERE link_type='REPLACES'") == 1
    ingest(
        engine,
        "c.csv",
        row("Acme", "C102", "2026-03-25", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213", freq="8", orig="C101"),
    )
    assert engine.db.scalar("SELECT status FROM invoices WHERE invoice_number_norm='C101'") == "VOID"


def test_prior_not_duplicate_review_suppresses(engine: Engine) -> None:
    from verismo_engine.review import decide
    from verismo_engine.security.users import create_user

    uid = create_user(engine.db, "rev", "Reviewer", "REVIEWER", "reviewer password")
    ingest(
        engine,
        "a.csv",
        row("Acme", "100", "2026-03-10", "480.00", 1, "Ann", "Lee", "2026-03-01", "99213", charge="480.00"),
    )
    ingest(
        engine,
        "b.csv",
        row("Acme", "200", "2026-03-10", "480.00", 1, "Bob", "Ray", "2026-03-02", "99214", charge="480.00"),
    )
    f = engine.db.one("SELECT * FROM flags WHERE rule_id='INV-003'")
    decide(engine, f["id"], "NOT_DUPLICATE", uid, reason_code="DIFFERENT_SERVICE")
    detect(engine)
    f2 = engine.db.one("SELECT * FROM flags WHERE id=?", (f["id"],))
    assert f2["suppressed_by"] == "SUP-005"
    assert f2["status"] == "DISMISSED"


def test_ambiguous_date_is_flagged_not_guessed(engine: Engine) -> None:
    out = ingest(engine, "a.csv", row("Acme", "9", "03/04/2026", "100.00", 1, "Ann", "Lee", "2026-03-01", "99213"))
    inv = engine.db.one("SELECT invoice_date, invoice_date_raw, needs_attention FROM invoices")
    assert inv["invoice_date"] is None and inv["invoice_date_raw"] == "03/04/2026"
    assert "ambiguous" in inv["needs_attention"]
    assert any("ambiguous" in i["message"] for i in out["issues"])


def test_patient_names_encrypted_at_field_level(engine: Engine) -> None:
    ingest(engine, "a.csv", row("Acme", "1", "2026-03-10", "100.00", 1, "Maria", "Garcia", "2026-03-04", "97110"))
    p = engine.db.one("SELECT * FROM patients")
    assert "GARCIA" not in (p["last_name_enc"] or "") and p["last_name_enc"].startswith("v1:")
    assert engine.field_cipher.decrypt(p["last_name_enc"], aad="patient.last") == "GARCIA"
