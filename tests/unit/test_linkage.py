"""Patient identity resolution: auto-link precision target 0.995 (spec 5.3) on a hard set."""

from __future__ import annotations

import datetime as dt
import random

from verismo_engine.context import Engine
from verismo_engine.ingest.model import InvoiceIn, LineIn, ParseResult, PartyIn, PatientIn
from verismo_engine.ingest.persist import persist
from verismo_engine.linkage.patients import resolve_patients, set_link
from verismo_engine.normalize import normalize_person_name

FIRST = [
    "James",
    "Mary",
    "Robert",
    "Linda",
    "Michael",
    "Susan",
    "William",
    "Karen",
    "David",
    "Nancy",
    "Carlos",
    "Priya",
    "Wei",
    "Olga",
    "Ahmed",
    "Grace",
    "Henry",
    "Irene",
    "Jorge",
    "Keiko",
]
LAST = [
    "Smith",
    "Garcia",
    "Nguyen",
    "Okafor",
    "Kowalski",
    "Patel",
    "Johnson",
    "Hernandez",
    "Chen",
    "Schmidt",
    "Rossi",
    "Silva",
    "Haddad",
    "Novak",
    "Dubois",
    "Yamamoto",
    "Ivanova",
    "Mbeki",
    "Costa",
    "Walker",
]
NICK = {"James": "Jim", "Robert": "Bob", "Michael": "Mike", "William": "Bill", "David": "Dave", "Susan": "Sue"}


def _typo(s: str, rng: random.Random) -> str:
    i = rng.randint(1, len(s) - 2)
    return s[:i] + s[i + 1] + s[i] + s[i + 2 :]


def test_identity_precision_on_hard_set(engine: Engine) -> None:
    rng = random.Random(5)
    people = []
    for i in range(400):
        dob = dt.date(1940, 1, 1) + dt.timedelta(days=rng.randint(0, 25000))
        people.append(
            (i, rng.choice(FIRST), rng.choice(LAST), dob.isoformat(), rng.choice("MF"), f"6{rng.randint(1000, 9999)}")
        )
    records = [(p, p[1], p[2], p[3]) for p in people]
    truth_dupes = 0
    for p in rng.sample(people, 60):  # duplicates with variants and NO shared member id
        kind = rng.choice(["typo", "nick", "swap", "typo"])
        first, last, dob = p[1], p[2], p[3]
        if kind == "nick" and first in NICK:
            first = NICK[first]
        elif kind == "swap" and int(dob[8:]) <= 12 and dob[5:7] != dob[8:]:
            dob = f"{dob[:4]}-{dob[8:]}-{dob[5:7]}"
        else:
            last = _typo(last, rng)
        records.append((p, first, last, dob))
        truth_dupes += 1
    for p in rng.sample(people, 30):  # look-alikes: same last name + DOB + zip, different first name
        other_first = rng.choice([f for f in FIRST if f != p[1] and NICK.get(f) != p[1]])
        q = (10_000 + p[0], other_first, p[2], p[3], p[4], p[5])
        records.append((q, other_first, p[2], p[3]))
    lines = []
    for n, (p, first, last, dob) in enumerate(records):
        lines.append(
            LineIn(
                n + 1,
                PatientIn(first, last, dob, p[4], p[5]),
                "2026-03-01",
                "2026-03-01",
                "99213",
                charge_cents=1000 + n,
            )
        )
    pr = ParseResult(
        method="CSV",
        invoices=[InvoiceIn("AP", PartyIn("Clinic", "VENDOR", "12-3456789"), "1", "2026-03-02", lines=lines)],
    )
    persist(engine, pr, sha256="x", source_path="x.csv", mime="CSV", user_id=None)
    res = resolve_patients(engine)
    assert res["method"] == "fellegi_sunter_em"
    key_to_person = {}
    for p, first, last, dob in records:
        nm = normalize_person_name(first, last)
        key_to_person[engine.hmac(f"{nm.first}|{nm.last}|{dob}", "patient_key")] = p[0]
    rows = engine.db.query("SELECT id, patient_key, cluster_id FROM patients")
    person = {r["id"]: key_to_person[r["patient_key"]] for r in rows}
    clusters: dict[int, list[int]] = {}
    for r in rows:
        clusters.setdefault(r["cluster_id"], []).append(r["id"])
    linked = [(a, b) for ids in clusters.values() for i, a in enumerate(ids) for b in ids[i + 1 :]]
    correct = sum(1 for a, b in linked if person[a] == person[b])
    precision = correct / len(linked) if linked else 1.0
    recall = correct / truth_dupes
    assert precision >= 0.995, (precision, len(linked))
    assert recall >= 0.6, recall  # the rest go to the identity review queue
    review = engine.db.scalar("SELECT COUNT(*) FROM identity_suggestions WHERE status='OPEN'")
    assert correct + review >= truth_dupes * 0.8
    # every suggestion carries explainable per-field weights
    ev = engine.db.scalar("SELECT evidence FROM identity_suggestions WHERE status IN ('OPEN','AUTO') LIMIT 1")
    assert "fellegi_sunter" in ev and "match_weight" in ev


def test_unlink_splits_cluster_and_is_audited(engine: Engine) -> None:
    lines = [
        LineIn(1, PatientIn("Ann", "Lee", "1980-01-02", member_id="M1"), "2026-03-01", code="99213"),
        LineIn(2, PatientIn("Ann", "Leee", "1980-01-02", member_id="M1"), "2026-03-02", code="99213"),
    ]
    pr = ParseResult(
        method="CSV", invoices=[InvoiceIn("AP", PartyIn("Clinic", "VENDOR"), "1", "2026-03-02", lines=lines)]
    )
    persist(engine, pr, sha256="y", source_path="y.csv", mime="CSV", user_id=None)
    from verismo_engine.linkage.patients import quick_link_new_patients

    ids = [r["id"] for r in engine.db.query("SELECT id FROM patients ORDER BY id")]
    quick_link_new_patients(engine, ids)
    assert len({r["cluster_id"] for r in engine.db.query("SELECT cluster_id FROM patients")}) == 1
    set_link(engine, ids[0], ids[1], link=False, user_id=None)
    assert len({r["cluster_id"] for r in engine.db.query("SELECT cluster_id FROM patients")}) == 2
    assert engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action='PATIENT_UNLINK'") == 1
