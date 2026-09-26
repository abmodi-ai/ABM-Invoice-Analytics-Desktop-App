"""Party (vendor / customer / payer) identity resolution.

1. Deterministic: party records of the same type sharing an exact tax ID HMAC or NPI (including
   aliases) are merged into one cluster. These are applied automatically and audited.
2. Probabilistic: name (Jaro-Winkler + token-set), remittance address and phone are scored with
   Fellegi-Sunter log-likelihood weights. Pairs above the threshold are *suggested* to an admin;
   they are never applied automatically. Party counts are small (hundreds), too few for stable EM
   estimation, so fixed m/u weights are used here; patients use the EM-trained Fellegi-Sunter model.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from invoice_analytics.context import Engine
from invoice_analytics.linkage.patients import recluster
from invoice_analytics.security import audit

# (m, u) per comparison level: P(level | match), P(level | non-match)
WEIGHTS = {
    "name_exact": (0.55, 0.001),
    "name_high": (0.35, 0.01),
    "name_mid": (0.07, 0.05),
    "name_low": (0.03, 0.939),
    "addr_exact": (0.6, 0.002),
    "addr_diff": (0.2, 0.9),
    "addr_null": (0.2, 0.098),
    "phone_exact": (0.5, 0.001),
    "phone_diff": (0.2, 0.9),
    "phone_null": (0.3, 0.099),
}
PRIOR = 0.01


def _w(level: str) -> float:
    m, u = WEIGHTS[level]
    return math.log2(m / u)


def score_pair(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    jw = JaroWinkler.similarity(a["name_norm"], b["name_norm"])
    ts = fuzz.token_set_ratio(a["name_norm"], b["name_norm"]) / 100
    sim = max(jw, ts)
    name = (
        "name_exact"
        if a["name_norm"] == b["name_norm"]
        else ("name_high" if sim >= 0.92 else ("name_mid" if sim >= 0.8 else "name_low"))
    )
    addr = (
        "addr_null"
        if not (a["remit_address_norm"] and b["remit_address_norm"])
        else ("addr_exact" if a["remit_address_norm"] == b["remit_address_norm"] else "addr_diff")
    )
    phone = (
        "phone_null"
        if not (a["phone_norm"] and b["phone_norm"])
        else ("phone_exact" if a["phone_norm"] == b["phone_norm"] else "phone_diff")
    )
    total = math.log2(PRIOR / (1 - PRIOR)) + _w(name) + _w(addr) + _w(phone)
    p = 2**total / (1 + 2**total)
    return p, {
        "name": {"level": name, "jaro_winkler": round(jw, 3), "token_set": round(ts, 3)},
        "remit_address": addr,
        "phone": phone,
        "match_weight": round(total, 3),
        "match_probability": round(p, 5),
        "method": "fellegi_sunter_fixed_weights",
    }


def deterministic_merges(eng: Engine, user_id: int | None = None) -> int:
    rows = eng.db.query(
        "SELECT p.id, p.party_type, p.tax_id_hmac AS v, 'TAX_ID' AS t FROM parties p WHERE p.tax_id_hmac IS NOT NULL"
        " UNION ALL SELECT p.id, p.party_type, p.npi, 'NPI' FROM parties p WHERE p.npi IS NOT NULL"
        " UNION ALL SELECT a.party_id, p.party_type, a.value_norm, a.alias_type FROM party_aliases a"
        " JOIN parties p ON p.id=a.party_id WHERE a.alias_type IN ('TAX_ID','NPI')"
    )
    groups: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for r in rows:
        groups[(r["party_type"], r["t"], r["v"])].add(r["id"])
    n = 0
    with eng.db.tx() as c:
        for (ptype, t, _v), ids in groups.items():
            if len(ids) < 2:
                continue
            ids_s = sorted(ids)
            for other in ids_s[1:]:
                cur = c.execute(
                    "INSERT OR IGNORE INTO identity_suggestions(entity_type, left_id, right_id, match_probability,"
                    " evidence, status) VALUES ('PARTY',?,?,1.0,?,'AUTO')",
                    (ids_s[0], other, json.dumps({"method": f"exact_{t.lower()}", "party_type": ptype})),
                )
                n += cur.rowcount
        if n:
            audit.record(
                eng.db,
                "PARTY_DETERMINISTIC_MERGE",
                user_id=user_id,
                entity_type="parties",
                after={"merged_pairs": n},
                conn=c,
            )
    if n:
        recluster(eng, "PARTY")
    return n


def suggest_merges(eng: Engine, user_id: int | None = None) -> dict[str, Any]:
    deterministic = deterministic_merges(eng, user_id)
    thr = float(eng.settings.get("linkage.party", {}).get("suggest_threshold", 0.85))
    parties = eng.db.query(
        "SELECT id, party_type, name_norm, remit_address_norm, phone_norm, cluster_id FROM parties" " WHERE active=1"
    )
    decided = {
        (r["left_id"], r["right_id"])
        for r in eng.db.query(
            "SELECT left_id, right_id FROM identity_suggestions WHERE entity_type='PARTY' AND status<>'OPEN'"
        )
    }
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in parties:
        by_type[p["party_type"]].append(p)
    new = 0
    with eng.db.tx() as c:
        for plist in by_type.values():
            # block on first name token or address or phone to avoid O(n^2) on large vendor masters
            blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for p in plist:
                tok = (p["name_norm"].split() or [""])[0]
                blocks[f"n:{tok[:4]}"].append(p)
                if p["remit_address_norm"]:
                    blocks[f"a:{p['remit_address_norm']}"].append(p)
                if p["phone_norm"]:
                    blocks[f"p:{p['phone_norm']}"].append(p)
            seen: set[tuple[int, int]] = set()
            for members in blocks.values():
                if len(members) > 300:
                    continue
                for i, a in enumerate(members):
                    for b in members[i + 1 :]:
                        k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                        if k in seen or k in decided or a["cluster_id"] == b["cluster_id"]:
                            continue
                        seen.add(k)
                        prob, ev = score_pair(a, b)
                        if prob >= thr:
                            c.execute(
                                "INSERT INTO identity_suggestions(entity_type, left_id, right_id, match_probability,"
                                " evidence, status) VALUES ('PARTY',?,?,?,?,'OPEN') ON CONFLICT(entity_type, left_id,"
                                " right_id) DO UPDATE SET match_probability=excluded.match_probability,"
                                " evidence=excluded.evidence WHERE identity_suggestions.status='OPEN'",
                                (k[0], k[1], prob, json.dumps(ev)),
                            )
                            new += 1
    return {"deterministic_merges": deterministic, "suggestions": new}


def merge(eng: Engine, left: int, right: int, user_id: int | None) -> None:
    a, b = min(left, right), max(left, right)
    with eng.db.tx() as c:
        before = c.execute("SELECT id, cluster_id FROM parties WHERE id IN (?,?)", (a, b)).fetchall()
        c.execute(
            "INSERT INTO identity_suggestions(entity_type, left_id, right_id, match_probability, evidence, status,"
            " decided_by, decided_at) VALUES ('PARTY',?,?,1.0,?, 'ACCEPTED', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
            " ON CONFLICT(entity_type, left_id, right_id) DO UPDATE SET status='ACCEPTED',"
            " decided_by=excluded.decided_by, decided_at=excluded.decided_at",
            (a, b, json.dumps({"method": "manual"}), user_id),
        )
        audit.record(
            eng.db,
            "PARTY_MERGE",
            user_id=user_id,
            entity_type="party_pair",
            entity_id=f"{a}-{b}",
            before=before,
            conn=c,
        )
    recluster(eng, "PARTY")


def unmerge(eng: Engine, left: int, right: int, user_id: int | None) -> None:
    a, b = min(left, right), max(left, right)
    with eng.db.tx() as c:
        before = c.execute("SELECT id, cluster_id FROM parties WHERE id IN (?,?)", (a, b)).fetchall()
        c.execute(
            "INSERT INTO identity_suggestions(entity_type, left_id, right_id, match_probability, evidence, status,"
            " decided_by, decided_at) VALUES ('PARTY',?,?,0.0,?, 'REJECTED', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
            " ON CONFLICT(entity_type, left_id, right_id) DO UPDATE SET status='REJECTED',"
            " decided_by=excluded.decided_by, decided_at=excluded.decided_at",
            (a, b, json.dumps({"method": "manual"}), user_id),
        )
        audit.record(
            eng.db,
            "PARTY_UNMERGE",
            user_id=user_id,
            entity_type="party_pair",
            entity_id=f"{a}-{b}",
            before=before,
            conn=c,
        )
    recluster(eng, "PARTY")
