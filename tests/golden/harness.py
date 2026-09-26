"""Golden-set harness: ingest a synthgen world, run identity resolution + full sweep, score flags.

Caveat printed with every report: the generator and the rules are written by the same team, so
synthetic metrics are necessary but not sufficient. Real de-identified client data (Phase 2
onwards) is the real acceptance gate.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from synthgen.generator import Generator, World
from synthgen.writers import CSV_MAPPING, render
from verismo_engine.context import Engine
from verismo_engine.ingest.service import ingest_bytes
from verismo_engine.linkage.patients import resolve_patients
from verismo_engine.rules.base import TIER_RANK
from verismo_engine.scoring.pipeline import detect

CAVEAT = (
    "Synthetic golden set: generator and rules share authors, so these numbers are a regression "
    "gate, not proof of real-world precision."
)


@dataclass
class GoldenReport:
    per_type: dict[str, dict[str, Any]] = field(default_factory=dict)
    tier_precision: dict[str, dict[str, Any]] = field(default_factory=dict)
    legit_false_positives: dict[str, int] = field(default_factory=dict)
    recall_probable_plus: float = 0.0
    recall_hard_exact: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "caveat": CAVEAT}

    def markdown(self) -> str:
        out = [
            "# Golden set report",
            "",
            f"> {CAVEAT}",
            "",
            f"Recall at PROBABLE+ (types expected at PROBABLE+): **{self.recall_probable_plus:.3f}**",
            f"Recall at HARD for injected exact duplicates: **{self.recall_hard_exact:.3f}**",
            "",
            "| tier | flags | true positive | precision |",
            "|---|---|---|---|",
        ]
        for t, v in self.tier_precision.items():
            out.append(f"| {t} | {v['flags']} | {v['tp']} | {v['precision']:.3f} |")
        out += [
            "",
            "| injected type | cases | detected at expected tier | recall | rules seen |",
            "|---|---|---|---|---|",
        ]
        for k, v in sorted(self.per_type.items()):
            out.append(f"| {k} | {v['cases']} | {v['detected']} | {v['recall']:.3f} | {', '.join(v['rules'])} |")
        out += ["", "| legitimate type | PROBABLE+ false positives |", "|---|---|"]
        for k, v in sorted(self.legit_false_positives.items()):
            out.append(f"| {k} | {v} |")
        out += [
            "",
            f"Identity: {json.dumps(self.identity)}",
            f"Timings: {json.dumps({k: round(v, 2) for k, v in self.timings.items()})}",
            f"Counts: {json.dumps(self.counts)}",
        ]
        return "\n".join(out)


def ingest_world(eng: Engine, world: World) -> dict[str, float]:
    docs = render(world)
    t0 = time.perf_counter()
    inc_times: list[float] = []
    for d in docs:
        for _ in range(2 if d.ingest_twice else 1):
            t = time.perf_counter()
            out = ingest_bytes(
                eng,
                d.name,
                d.data,
                mapping=CSV_MAPPING if d.kind == "CSV" else None,
                options={"direction": "AP", "strict_dates": True},
            )
            if out.persist and out.persist.invoice_ids:
                inc_times.append((time.perf_counter() - t) / len(out.persist.invoice_ids))
    inc_times.sort()
    return {
        "ingest_total_s": time.perf_counter() - t0,
        "incremental_per_invoice_p50_s": inc_times[len(inc_times) // 2] if inc_times else 0.0,
        "incremental_per_invoice_p95_s": inc_times[int(len(inc_times) * 0.95)] if inc_times else 0.0,
    }


def _invoice_index(eng: Engine) -> dict[str, list[int]]:
    idx: dict[str, list[int]] = defaultdict(list)
    for r in eng.db.query("SELECT id, invoice_number_raw, invoice_date, total_cents FROM invoices"):
        idx[f"{r['invoice_number_raw']}|{r['invoice_date']}|{r['total_cents']}"].append(r["id"])
    return idx


def identity_metrics(eng: Engine, world: World) -> dict[str, Any]:
    """Pairwise precision/recall of patient clusters against the generator's person ids."""
    from verismo_engine.normalize import normalize_person_name

    key_to_person: dict[str, int] = {}
    for inv in world.invoices:
        for li in inv.lines:
            if li.patient is None:
                continue
            o = li.patient_override or {}
            n = normalize_person_name(o.get("first", li.patient.first), o.get("last", li.patient.last))
            dob = o.get("dob", li.patient.dob)
            key_to_person[eng.hmac(f"{n.first}|{n.last}|{dob}", "patient_key")] = li.patient.pid
    rows = eng.db.query("SELECT id, patient_key, cluster_id FROM patients")
    person = {r["id"]: key_to_person.get(r["patient_key"]) for r in rows}
    cluster = {r["id"]: r["cluster_id"] for r in rows}
    by_person: dict[int, list[int]] = defaultdict(list)
    by_cluster: dict[int, list[int]] = defaultdict(list)
    for pid, per in person.items():
        if per is not None:
            by_person[per].append(pid)
        by_cluster[cluster[pid]].append(pid)
    true_pairs = {(min(a, b), max(a, b)) for ids in by_person.values() for i, a in enumerate(ids) for b in ids[i + 1 :]}
    pred_pairs = {
        (min(a, b), max(a, b)) for ids in by_cluster.values() for i, a in enumerate(ids) for b in ids[i + 1 :]
    }
    tp = len(true_pairs & pred_pairs)
    return {
        "true_pairs": len(true_pairs),
        "linked_pairs": len(pred_pairs),
        "correct_links": tp,
        "link_precision": tp / len(pred_pairs) if pred_pairs else 1.0,
        "link_recall": tp / len(true_pairs) if true_pairs else 1.0,
        "open_review_suggestions": eng.db.scalar(
            "SELECT COUNT(*) FROM identity_suggestions WHERE entity_type='PATIENT' AND status='OPEN'"
        ),
    }


def evaluate(eng: Engine, world: World) -> GoldenReport:
    rep = GoldenReport()
    idx = _invoice_index(eng)
    flags = eng.db.query(
        "SELECT id, rule_id, tier, subject_invoice_id, counterpart_invoice_ids FROM flags"
        " WHERE active=1 AND suppressed_by IS NULL"
    )
    inv_flags: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for f in flags:
        ids = {f["subject_invoice_id"], *json.loads(f["counterpart_invoice_ids"])}
        f["_ids"] = ids
        for i in ids:
            inv_flags[i].append(f)
    dup_ids: set[int] = set()
    related: set[int] = set()
    legit_ids: dict[str, set[int]] = defaultdict(set)
    per_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"cases": 0, "detected": 0, "rules": set()})
    exp_pp = [0, 0]
    exp_hard = [0, 0]
    for c in world.cases:
        ids = [i for k in c.invoices for i in idx.get(k, [])]
        orig = [i for k in c.originals for i in idx.get(k, [])]
        if not c.duplicate:
            legit_ids[c.kind].update(ids)
            continue
        dup_ids.update(ids)
        related.update(ids + orig)
        want = TIER_RANK[c.expected_min_tier]
        hit = False
        rules: set[str] = set()
        for i in ids:
            for f in inv_flags.get(i, []):
                rules.add(f["rule_id"])
                if TIER_RANK[f["tier"]] >= want:
                    hit = True
        pt = per_type[c.kind]
        pt["cases"] += 1
        pt["detected"] += int(hit)
        pt["rules"] |= rules
        if want >= TIER_RANK["PROBABLE"]:
            exp_pp[0] += 1
            exp_pp[1] += int(
                any(TIER_RANK[f["tier"]] >= TIER_RANK["PROBABLE"] for i in ids for f in inv_flags.get(i, []))
            )
        if c.kind in ("exact_resubmit", "same_file_twice", "renumbered_rebill"):
            exp_hard[0] += 1
            exp_hard[1] += int(any(f["tier"] == "HARD" for i in ids for f in inv_flags.get(i, [])))
    for k, v in per_type.items():
        rep.per_type[k] = {
            "cases": v["cases"],
            "detected": v["detected"],
            "recall": v["detected"] / v["cases"] if v["cases"] else 1.0,
            "rules": sorted(v["rules"]),
        }
    for tier in ("HARD", "PROBABLE", "WEAK"):
        tf = [f for f in flags if f["tier"] == tier]
        tp = sum(1 for f in tf if f["_ids"] & dup_ids and f["_ids"] <= related)
        rep.tier_precision[tier] = {"flags": len(tf), "tp": tp, "precision": tp / len(tf) if tf else 1.0}
    for kind, ids in legit_ids.items():
        rep.legit_false_positives[kind] = sum(
            1
            for f in flags
            if TIER_RANK[f["tier"]] >= TIER_RANK["PROBABLE"] and f["_ids"] & ids and not f["_ids"] & dup_ids
        )
    rep.recall_probable_plus = exp_pp[1] / exp_pp[0] if exp_pp[0] else 1.0
    rep.recall_hard_exact = exp_hard[1] / exp_hard[0] if exp_hard[0] else 1.0
    rep.counts = {
        "invoices": eng.db.scalar("SELECT COUNT(*) FROM invoices"),
        "lines": eng.db.scalar("SELECT COUNT(*) FROM invoice_lines"),
        "flags_active": len(flags),
        "cases": len(world.cases),
    }
    return rep


def run_golden(
    eng: Engine, seed: int = 42, n_invoices: int = 2500, n_patients: int = 1200, n_vendors: int = 30
) -> GoldenReport:
    world = Generator(seed, n_vendors, n_patients, n_invoices).build()
    timings = ingest_world(eng, world)
    t = time.perf_counter()
    ident = resolve_patients(eng)
    timings["identity_s"] = time.perf_counter() - t
    t = time.perf_counter()
    res = detect(eng)
    timings["full_sweep_s"] = time.perf_counter() - t
    rep = evaluate(eng, world)
    rep.timings = timings
    rep.identity = {**identity_metrics(eng, world), "method": ident["method"]}
    rep.counts["rule_errors"] = len(res.errors)
    return rep
