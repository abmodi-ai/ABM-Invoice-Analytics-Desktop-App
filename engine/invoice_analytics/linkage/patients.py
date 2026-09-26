"""Patient identity resolution.

quick_link_new_patients: deterministic, runs at ingest (same source ID + same DOB).
resolve_patients: Fellegi-Sunter (u from random pairs, m and prior by EM) over names decrypted
in memory for the duration of the run only. Pairs above the auto-link
threshold are linked; the middle band goes to the identity review queue.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.linkage.clusters import UnionFind
from invoice_analytics.normalize.names import canonical_first_name, phonetic
from invoice_analytics.security import audit

log = logging.getLogger("invoice_analytics.linkage.patients")


def quick_link_new_patients(eng: Engine, patient_ids: list[int]) -> int:
    """Same MEMBER_ID/MRN (keyed hash) from the same source and same DOB -> same person."""
    if not patient_ids:
        return 0
    ph = ",".join("?" * len(patient_ids))
    rows = eng.db.query(
        f"SELECT n.patient_id AS new_id, o.patient_id AS old_id, po.cluster_id AS old_cluster"
        f" FROM patient_source_ids n JOIN patient_source_ids o ON o.value_hmac=n.value_hmac"
        f" AND o.id_type=n.id_type AND o.patient_id<>n.patient_id"
        f" AND COALESCE(o.source_party_id,0)=COALESCE(n.source_party_id,0)"
        f" JOIN patients pn ON pn.id=n.patient_id JOIN patients po ON po.id=o.patient_id"
        f" WHERE n.patient_id IN ({ph}) AND pn.dob IS NOT NULL AND pn.dob=po.dob AND o.patient_id < n.patient_id",
        patient_ids,
    )
    if not rows:
        return 0
    with eng.db.tx() as c:
        for r in rows:
            c.execute("UPDATE patients SET cluster_id=? WHERE id=?", (r["old_cluster"], r["new_id"]))
            c.execute(
                "INSERT OR IGNORE INTO identity_suggestions(entity_type, left_id, right_id, match_probability,"
                " evidence, status) VALUES ('PATIENT',?,?,1.0,?, 'AUTO')",
                (r["old_id"], r["new_id"], json.dumps({"method": "source_id+dob"})),
            )
    return len(rows)


def _patient_frame(eng: Engine) -> list[dict[str, Any]]:
    fc = eng.field_cipher
    rows = eng.db.query(
        "SELECT p.id, p.first_name_enc, p.last_name_enc, p.dob, p.sex, p.zip5, p.cluster_id,"
        " (SELECT group_concat(value_hmac) FROM patient_source_ids s WHERE s.patient_id=p.id) AS src"
        " FROM patients p WHERE p.deleted_at IS NULL"
    )
    out = []
    for r in rows:
        first = fc.decrypt(r["first_name_enc"], aad="patient.first") or ""
        last = fc.decrypt(r["last_name_enc"], aad="patient.last") or ""
        dob = r["dob"] or ""
        try:
            d = dt.date.fromisoformat(dob) if dob else None
        except ValueError:
            d = None
        out.append(
            {
                "unique_id": r["id"],
                "first_name": first or None,
                "first_canon": canonical_first_name(first) if first else None,
                "last_name": last or None,
                "last_phonetic": phonetic(last) if last else None,
                "dob": dob or None,
                "dob_swapped": (f"{d.year:04d}-{d.day:02d}-{d.month:02d}" if d and d.day <= 12 else None),
                "dob_year": d.year if d else None,
                "dob_md": f"{d.month:02d}-{d.day:02d}" if d else None,
                "sex": r["sex"],
                "zip5": r["zip5"],
                "src": r["src"] or None,
                "cluster_id": r["cluster_id"],
            }
        )
    return out


def _jw(x: str | None, y: str | None) -> float:
    from rapidfuzz.distance import JaroWinkler

    return JaroWinkler.similarity(x, y) if x and y else 0.0


def _first_level(a: dict[str, Any], b: dict[str, Any]) -> int | None:
    if not a["first_name"] or not b["first_name"]:
        return None
    if a["first_name"] == b["first_name"]:
        return 0
    if a["first_canon"] == b["first_canon"]:
        return 1
    return 2 if _jw(a["first_name"], b["first_name"]) >= 0.9 else 3


def _last_level(a: dict[str, Any], b: dict[str, Any]) -> int | None:
    if not a["last_name"] or not b["last_name"]:
        return None
    if a["last_name"] == b["last_name"]:
        return 0
    if _jw(a["last_name"], b["last_name"]) >= 0.92:
        return 1
    return 2 if a["last_phonetic"] and a["last_phonetic"] == b["last_phonetic"] else 3


def _dob_level(a: dict[str, Any], b: dict[str, Any]) -> int | None:
    if not a["dob"] or not b["dob"]:
        return None
    if a["dob"] == b["dob"]:
        return 0
    if a["dob_swapped"] and a["dob_swapped"] == b["dob"]:
        return 1
    return 2 if a["dob_md"] == b["dob_md"] and abs(a["dob_year"] - b["dob_year"]) == 1 else 3


def _eq(field: str) -> Any:
    def fn(a: dict[str, Any], b: dict[str, Any]) -> int | None:
        if not a[field] or not b[field]:
            return None
        return 0 if a[field] == b[field] else 1

    return fn


def _src_level(a: dict[str, Any], b: dict[str, Any]) -> int | None:
    if not a["src"] or not b["src"]:
        return None
    return 0 if set(a["src"].split(",")) & set(b["src"].split(",")) else 1


def patient_model() -> Any:
    from invoice_analytics.linkage.fellegi_sunter import Comparison, Model

    return Model(
        comparisons=[
            Comparison(
                "first_name",
                ["exact", "nickname", "jaro_winkler>=0.9", "else"],
                _first_level,
                [0.8, 0.08, 0.07, 0.05],
                0.05,
            ),
            Comparison(
                "last_name",
                ["exact", "jaro_winkler>=0.92", "phonetic", "else"],
                _last_level,
                [0.85, 0.08, 0.04, 0.03],
                0.05,
            ),
            Comparison(
                "dob",
                ["exact", "day/month transposed", "year off by one", "else"],
                _dob_level,
                [0.9, 0.04, 0.03, 0.03],
                0.05,
            ),
            Comparison("sex", ["exact", "else"], _eq("sex"), [0.97, 0.03], 0.05),
            Comparison("zip5", ["exact", "else"], _eq("zip5"), [0.85, 0.15]),
            Comparison("source_id", ["shared", "else"], _src_level, [0.7, 0.3]),
        ],
        blocking=[
            lambda r: r["dob"],
            lambda r: (r["last_phonetic"], r["dob_year"]) if r["last_phonetic"] and r["dob_year"] else None,
            lambda r: (r["last_name"], r["first_canon"]) if r["last_name"] and r["first_canon"] else None,
            lambda r: min(r["dob"], r["dob_swapped"]) if r["dob"] and r["dob_swapped"] else None,
            lambda r: r["src"].split(",") if r["src"] else None,
        ],
    )


def _fallback_score(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Deterministic scorer used when the dataset is too small for EM estimation."""
    from rapidfuzz.distance import JaroWinkler

    s = 0.0
    if a["last_name"] and b["last_name"]:
        jw = JaroWinkler.similarity(a["last_name"], b["last_name"])
        s += 3.0 if jw == 1 else (2.0 if jw >= 0.92 or a["last_phonetic"] == b["last_phonetic"] else -3.0)
    if a["first_name"] and b["first_name"]:
        if a["first_canon"] == b["first_canon"]:
            s += 2.0
        elif JaroWinkler.similarity(a["first_name"], b["first_name"]) >= 0.9:
            s += 1.0
        else:
            s -= 2.0
    if a["dob"] and b["dob"]:
        if a["dob"] == b["dob"]:
            s += 4.0
        elif a["dob_swapped"] == b["dob"] or (a["dob_md"] == b["dob_md"] and abs(a["dob_year"] - b["dob_year"]) == 1):
            s += 1.5
        else:
            s -= 5.0
    if a["sex"] and b["sex"] and a["sex"] != b["sex"]:
        s -= 2.0
    if a["zip5"] and b["zip5"]:
        s += 0.5 if a["zip5"] == b["zip5"] else -0.5
    if a["src"] and b["src"] and set(a["src"].split(",")) & set(b["src"].split(",")):
        s += 3.0
    import math

    return 1 / (1 + math.exp(-(s - 6.0)))


def _candidate_pairs_fallback(recs: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    from collections import defaultdict

    blocks: dict[tuple[str, Any], list[int]] = defaultdict(list)
    for i, r in enumerate(recs):
        if r["dob"]:
            blocks[("dob", r["dob"])].append(i)
        if r["last_phonetic"] and r["dob_year"]:
            blocks[("ph", (r["last_phonetic"], r["dob_year"]))].append(i)
        if r["dob_swapped"]:
            blocks[("dob", r["dob_swapped"])].append(i)
    seen: set[tuple[int, int]] = set()
    out = []
    for idx in blocks.values():
        if len(idx) > 200:
            continue
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                a, b = recs[i]["unique_id"], recs[j]["unique_id"]
                k = (min(a, b), max(a, b))
                if k in seen:
                    continue
                seen.add(k)
                out.append((k[0], k[1], _fallback_score(recs[i], recs[j])))
    return out


def _fs_pairs(
    recs: list[dict[str, Any]], review_threshold: float
) -> tuple[list[tuple[int, int, float]], dict[int, dict[str, Any]]]:
    """Fellegi-Sunter with u from random pairs and m / prior by EM on blocked pairs."""
    model = patient_model()
    pairs = model.candidate_pairs(recs)
    if not pairs:
        return [], {}
    v = model.vectors(recs, pairs)
    model.estimate_u(recs)
    model.estimate_em(v)
    preds = model.predict(recs, pairs, v, threshold=review_threshold)
    out, expl = [], {}
    for i, j, p, why in preds:
        a, b = recs[i]["unique_id"], recs[j]["unique_id"]
        k = (min(a, b), max(a, b))
        out.append((k[0], k[1], p))
        expl[hash(k)] = why
    return out, expl


def resolve_patients(eng: Engine, *, method: str = "auto", user_id: int | None = None) -> dict[str, Any]:
    cfg = eng.settings.get("linkage.patient")
    auto_thr = float(cfg.get("auto_link_threshold", 0.995))
    review_thr = float(cfg.get("review_threshold", 0.80))
    recs = _patient_frame(eng)
    used = method
    if method == "auto":
        used = "fellegi_sunter_em" if len(recs) >= 200 else "fallback"
    pairs: list[tuple[int, int, float]]
    expl: dict[int, dict[str, Any]] = {}
    if used == "fellegi_sunter_em":
        try:
            pairs, expl = _fs_pairs(recs, review_thr)
        except Exception as e:  # noqa: BLE001 - EM can fail on degenerate data
            log.warning("linkage EM failed (%s); using deterministic fallback", type(e).__name__)
            used = "fallback"
            pairs = _candidate_pairs_fallback(recs)
    else:
        pairs = _candidate_pairs_fallback(recs)
    by_id = {r["unique_id"]: r for r in recs}
    rejected = {
        (r["left_id"], r["right_id"])
        for r in eng.db.query(
            "SELECT left_id, right_id FROM identity_suggestions WHERE entity_type='PATIENT' AND status='REJECTED'"
        )
    }
    accepted = [
        (r["left_id"], r["right_id"])
        for r in eng.db.query(
            "SELECT left_id, right_id FROM identity_suggestions WHERE entity_type='PATIENT' AND status IN ('ACCEPTED','AUTO')"
        )
    ]
    uf = UnionFind()
    for r in recs:
        uf.add(r["unique_id"])
    for a, b in accepted:
        if a in by_id and b in by_id:
            uf.union(a, b)
    auto, review = 0, 0
    with eng.db.tx() as c:
        for a, b, p in pairs:
            if (a, b) in rejected or p < review_thr:
                continue
            ev = _pair_evidence(by_id[a], by_id[b], p, used)
            if hash((a, b)) in expl:
                ev["fellegi_sunter"] = expl[hash((a, b))]
            # Conservative auto-link policy: a complete disagreement on first name, last name or DOB
            # never auto-links, whatever the score (relatives break the independence assumption:
            # twins share surname, DOB and ZIP). Such pairs go to the identity review queue.
            fs_fields = ev.get("fellegi_sunter", {}).get("fields", {})
            hard_disagree = (
                any(fs_fields.get(f, {}).get("level") in ("else", "null") for f in ("first_name", "last_name", "dob"))
                if fs_fields
                else False
            )
            if used == "fallback":
                hard_disagree = (
                    ev["dob"] == "different"
                    or (ev["first_name"]["jaro_winkler"] or 0) < 0.9
                    and not ev["first_name"]["nickname"]
                )
            if p >= auto_thr and not hard_disagree:
                uf.union(a, b)
                status = "AUTO"
                auto += 1
            else:
                status = "OPEN"
                review += 1
            c.execute(
                "INSERT INTO identity_suggestions(entity_type, left_id, right_id, match_probability, evidence, status)"
                " VALUES ('PATIENT',?,?,?,?,?) ON CONFLICT(entity_type, left_id, right_id) DO UPDATE SET"
                " match_probability=excluded.match_probability, evidence=excluded.evidence,"
                " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                " WHERE identity_suggestions.status IN ('OPEN','AUTO')",
                (a, b, p, json.dumps(ev), status),
            )
        changed = 0
        for pid in by_id:
            root = uf.find(pid)
            cluster = min(uf.members(root))
            if by_id[pid]["cluster_id"] != cluster:
                c.execute(
                    "UPDATE patients SET cluster_id=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                    (cluster, pid),
                )
                changed += 1
        audit.record(
            eng.db,
            "PATIENT_LINKAGE_RUN",
            user_id=user_id,
            entity_type="patients",
            after={
                "method": used,
                "pairs": len(pairs),
                "auto_linked": auto,
                "for_review": review,
                "clusters_changed": changed,
            },
            conn=c,
        )
    del recs, by_id
    eng.store.refresh_clusters()
    return {"method": used, "pairs": len(pairs), "auto_linked": auto, "for_review": review, "clusters_changed": changed}


def _pair_evidence(a: dict[str, Any], b: dict[str, Any], p: float, method: str) -> dict[str, Any]:
    """Evidence for reviewers: field agreement only, never the plaintext names."""
    from rapidfuzz.distance import JaroWinkler

    def jw(x: str | None, y: str | None) -> float | None:
        return round(JaroWinkler.similarity(x, y), 3) if x and y else None

    return {
        "method": method,
        "match_probability": round(p, 5),
        "first_name": {
            "jaro_winkler": jw(a["first_name"], b["first_name"]),
            "nickname": bool(a["first_canon"] and a["first_canon"] == b["first_canon"]),
        },
        "last_name": {
            "jaro_winkler": jw(a["last_name"], b["last_name"]),
            "phonetic": bool(a["last_phonetic"] and a["last_phonetic"] == b["last_phonetic"]),
        },
        "dob": (
            "exact"
            if a["dob"] and a["dob"] == b["dob"]
            else (
                "transposed"
                if a["dob_swapped"] and a["dob_swapped"] == b["dob"]
                else "year_off_by_one" if a["dob_md"] and a["dob_md"] == b["dob_md"] else "different"
            )
        ),
        "sex": a["sex"] == b["sex"] if a["sex"] and b["sex"] else None,
        "zip5": a["zip5"] == b["zip5"] if a["zip5"] and b["zip5"] else None,
        "source_id": bool(a["src"] and b["src"] and set(a["src"].split(",")) & set(b["src"].split(","))),
    }


def set_link(eng: Engine, left: int, right: int, *, link: bool, user_id: int | None) -> None:
    """Reviewer decision on a patient pair. Unlink splits the pair's cluster by re-running clustering."""
    a, b = min(left, right), max(left, right)
    with eng.db.tx() as c:
        before = c.execute("SELECT id, cluster_id FROM patients WHERE id IN (?,?)", (a, b)).fetchall()
        c.execute(
            "INSERT INTO identity_suggestions(entity_type, left_id, right_id, match_probability, evidence, status,"
            " decided_by, decided_at) VALUES ('PATIENT',?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
            " ON CONFLICT(entity_type, left_id, right_id) DO UPDATE SET status=excluded.status,"
            " decided_by=excluded.decided_by, decided_at=excluded.decided_at",
            (a, b, 1.0 if link else 0.0, json.dumps({"method": "manual"}), "ACCEPTED" if link else "REJECTED", user_id),
        )
        audit.record(
            eng.db,
            "PATIENT_LINK" if link else "PATIENT_UNLINK",
            user_id=user_id,
            entity_type="patient_pair",
            entity_id=f"{a}-{b}",
            before=before,
            conn=c,
        )
    recluster(eng, "PATIENT")


def recluster(eng: Engine, entity: str) -> None:
    """Rebuild cluster ids from ACCEPTED/AUTO suggestions (used after unlink/unmerge)."""
    table = "patients" if entity == "PATIENT" else "parties"
    ids = [r["id"] for r in eng.db.query(f"SELECT id FROM {table}")]
    uf = UnionFind()
    for i in ids:
        uf.add(i)
    for r in eng.db.query(
        "SELECT left_id, right_id FROM identity_suggestions WHERE entity_type=? AND status IN ('ACCEPTED','AUTO')",
        (entity,),
    ):
        if r["left_id"] in uf.parent and r["right_id"] in uf.parent:
            uf.union(r["left_id"], r["right_id"])
    with eng.db.tx() as c:
        for i in ids:
            c.execute(f"UPDATE {table} SET cluster_id=? WHERE id=?", (min(uf.members(uf.find(i))), i))
    eng.store.refresh_clusters()
