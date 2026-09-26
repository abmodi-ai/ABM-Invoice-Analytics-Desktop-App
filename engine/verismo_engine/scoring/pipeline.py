"""Detection pipeline: rule passes -> suppression -> tiering -> evidence -> flags.

detect(eng)                     full sweep
detect(eng, invoice_ids=[...])  incremental (new records against all history)
detect(eng, persist=False, settings_override={...})  impact preview (no writes)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from verismo_engine.context import Engine
from verismo_engine.rules import ALL_RULES, RULES_BY_ID, Candidate, RuleContext
from verismo_engine.rules.base import TIER_RANK
from verismo_engine.scoring.evidence import build_evidence
from verismo_engine.scoring.suppress import SuppressionData, apply_suppressions, pair_key
from verismo_engine.security import audit

log = logging.getLogger("verismo.detect")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def counterpart_hash(ids: tuple[int, ...] | list[int]) -> str:
    return hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:32]


@dataclass
class DetectionResult:
    run_id: int | None
    mode: str
    candidates: list[Candidate] = field(default_factory=list)
    new_flag_ids: list[int] = field(default_factory=list)
    updated_flag_ids: list[int] = field(default_factory=list)
    deactivated: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        by_rule: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        suppressed = 0
        for c in self.candidates:
            by_rule[c.rule_id] = by_rule.get(c.rule_id, 0) + 1
            if c.suppressed_by:
                suppressed += 1
            else:
                by_tier[c.tier] = by_tier.get(c.tier, 0) + 1
        return {
            "mode": self.mode,
            "candidates": len(self.candidates),
            "suppressed": suppressed,
            "by_rule": by_rule,
            "by_tier": by_tier,
            "new_flags": len(self.new_flag_ids),
            "updated_flags": len(self.updated_flag_ids),
            "deactivated": self.deactivated,
            "timings": {k: round(v, 4) for k, v in self.timings.items()},
            "errors": self.errors,
        }


def _prefetch(eng: Engine, cands: list[Candidate]) -> tuple[SuppressionData, dict[int, dict[str, Any]]]:
    store = eng.store
    line_ids: set[int] = set()
    inv_ids: set[int] = set()
    for c in cands:
        ids = (c.subject_id, *c.counterpart_ids)
        (line_ids if c.subject_type == "LINE" else inv_ids).update(ids)
    lines = store.fetch("lines", list(line_ids))
    inv_ids.update(v["invoice_id"] for v in lines.values())
    invoices = store.fetch("inv", list(inv_ids))
    links: set[tuple[int, int, str]] = set()
    netted: set[int] = set()
    display: dict[int, dict[str, Any]] = {}
    inv_list = sorted(inv_ids)
    for i in range(0, len(inv_list), 900):
        chunk = inv_list[i : i + 900]
        ph = ",".join("?" * len(chunk))
        for r in eng.db.query(
            f"SELECT from_invoice_id, to_invoice_id, link_type FROM invoice_links "
            f"WHERE from_invoice_id IN ({ph}) OR to_invoice_id IN ({ph})",
            chunk + chunk,
        ):
            links.add((r["from_invoice_id"], r["to_invoice_id"], r["link_type"]))
        for r in eng.db.query(
            f"SELECT i.id, i.invoice_number_raw, p.display_name AS party_name FROM invoices i "
            f"JOIN parties p ON p.id=i.party_id WHERE i.id IN ({ph})",
            chunk,
        ):
            display[r["id"]] = r
    # originals netted out by a credit from the same party cluster
    if invoices:
        con = store.con
        import pyarrow as pa

        con.register("_n", pa.table({"id": pa.array(sorted(invoices), pa.int64())}))
        try:
            for (oid,) in con.execute(
                "SELECT o.id FROM inv o JOIN _n ON _n.id = o.id JOIN inv cr ON cr.cluster = o.cluster"
                " AND cr.total = -o.total AND cr.total < 0 AND cr.id <> o.id"
                " AND (cr.orig_ref = o.num_norm OR cr.num_norm = o.num_norm OR cr.sort_key > o.sort_key)"
            ).fetchall():
                netted.add(int(oid))
        finally:
            con.unregister("_n")
    not_dup = {
        r["pair_key"] for r in eng.db.query("SELECT DISTINCT pair_key FROM reviews WHERE decision='NOT_DUPLICATE'")
    }
    recurring: dict[str, list[str]] = {}
    for r in eng.db.query("SELECT code, typical_frequency FROM ref_recurring_series"):
        recurring.setdefault(r["code"], []).append(r["typical_frequency"])
    return SuppressionData(invoices, lines, links, not_dup, netted, recurring), display


def detect(
    eng: Engine,
    invoice_ids: list[int] | None = None,
    *,
    persist: bool = True,
    settings_override: dict[str, Any] | None = None,
    rule_ids: list[str] | None = None,
    user_id: int | None = None,
) -> DetectionResult:
    mode = "PREVIEW" if not persist else ("INCREMENTAL" if invoice_ids is not None else "FULL")
    cfg = eng.settings.all()
    if settings_override:
        for k, v in settings_override.items():
            cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    refv = eng.refdata_version()
    run_id = None
    if persist:
        with eng.db.tx() as c:
            run_id = int(
                c.execute(
                    "INSERT INTO detection_runs(mode, engine_version, refdata_version, started_at) VALUES (?,?,?,?)",
                    (mode, eng.engine_version, refv, _now()),
                ).lastrowid
            )
    res = DetectionResult(run_id, mode)
    t0 = time.perf_counter()
    rules = [RULES_BY_ID[r] for r in rule_ids] if rule_ids else ALL_RULES
    store = eng.store
    try:
        with store.locked() as con:
            res.timings["load"] = time.perf_counter() - t0
            store.scope_table(invoice_ids)
            cands: list[Candidate] = []
            for rule in rules:
                rcfg = cfg.get(f"rules.{rule.rule_id}", {})
                if not rcfg.get("enabled", True):
                    continue
                tr = time.perf_counter()
                ctx = RuleContext(
                    store=store, con=con, config=rcfg, incremental=invoice_ids is not None, all_settings=cfg
                )
                try:
                    found = rule.run(ctx)
                except Exception as e:  # a failing rule must not stop the others
                    log.exception("rule %s failed", rule.rule_id)
                    res.errors[rule.rule_id] = f"{type(e).__name__}: {e}"
                    found = []
                for c in found:
                    c.base_tier = c.tier
                cands.extend(found)
                res.timings[rule.rule_id] = time.perf_counter() - tr
            # de-duplicate identical keys (a rule may emit a pair twice through different joins)
            uniq: dict[tuple[Any, ...], Candidate] = {}
            for c in cands:
                ck = c.key()
                if ck not in uniq or TIER_RANK[c.tier] > TIER_RANK[uniq[ck].tier]:
                    uniq[ck] = c
            cands = list(uniq.values())
            ts = time.perf_counter()
            data, display = _prefetch(eng, cands)
            apply_suppressions(cands, data, cfg)
            res.timings["suppress"] = time.perf_counter() - ts
            res.candidates = cands
            if persist:
                tp = time.perf_counter()
                _persist_flags(eng, res, data, display, refv)
                res.timings["persist"] = time.perf_counter() - tp
    except Exception as e:
        if persist and run_id:
            with eng.db.tx() as c:
                c.execute(
                    "UPDATE detection_runs SET status='FAILED', finished_at=?, error=? WHERE id=?",
                    (_now(), f"{type(e).__name__}: {e}", run_id),
                )
        raise
    res.timings["total"] = time.perf_counter() - t0
    if persist and run_id:
        with eng.db.tx() as c:
            c.execute(
                "UPDATE detection_runs SET status='DONE', finished_at=?, stats=? WHERE id=?",
                (_now(), json.dumps(res.stats()), run_id),
            )
            audit.record(
                eng.db,
                "DETECTION_RUN",
                user_id=user_id,
                entity_type="detection_run",
                entity_id=run_id,
                after={k: v for k, v in res.stats().items() if k != "timings"},
                conn=c,
            )
        _enqueue_ai(eng, res)
    return res


_UPSERT = (
    "INSERT INTO flags(rule_id, rule_version, engine_version, refdata_version, subject_type, subject_id,"
    " subject_invoice_id, counterpart_ids, counterpart_ids_hash, counterpart_invoice_ids, party_id, tier,"
    " base_tier, score, suppressed_by, downgraded_by, evidence, amount_at_risk_cents, last_run_id, updated_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    " ON CONFLICT(rule_id, subject_type, subject_id, counterpart_ids_hash) DO UPDATE SET"
    " rule_version=excluded.rule_version, engine_version=excluded.engine_version,"
    " refdata_version=excluded.refdata_version, tier=excluded.tier, base_tier=excluded.base_tier,"
    " score=excluded.score, suppressed_by=excluded.suppressed_by, downgraded_by=excluded.downgraded_by,"
    " evidence=excluded.evidence, amount_at_risk_cents=excluded.amount_at_risk_cents, active=1,"
    " last_run_id=excluded.last_run_id, updated_at=excluded.updated_at"
)
_KEY = "rule_id || '|' || subject_type || '|' || subject_id || '|' || counterpart_ids_hash"
BATCH = 5000


def _persist_flags(
    eng: Engine, res: DetectionResult, data: SuppressionData, display: dict[int, dict[str, Any]], refv: str
) -> None:
    """Build evidence and upsert in batches so memory stays flat on large sweeps."""
    ts = _now()
    new_ids: list[int] = []
    upd_ids: list[int] = []
    with eng.db.tx() as conn:
        for start in range(0, len(res.candidates), BATCH):
            batch = res.candidates[start : start + BATCH]
            rows = []
            for c in batch:
                rule = RULES_BY_ID[c.rule_id]
                ev = build_evidence(c, rule, data.invoices, data.lines, display)
                if c.subject_type == "LINE":
                    s_inv = data.lines[c.subject_id]["invoice_id"]
                    cp_inv = sorted({data.lines[i]["invoice_id"] for i in c.counterpart_ids if i in data.lines})
                else:
                    s_inv = c.subject_id
                    cp_inv = sorted(set(c.counterpart_ids))
                party = data.invoices.get(s_inv, {}).get("party_id")
                rows.append(
                    (
                        c.rule_id,
                        rule.version,
                        eng.engine_version,
                        refv if rule.uses_refdata else None,
                        c.subject_type,
                        c.subject_id,
                        s_inv,
                        json.dumps(sorted(c.counterpart_ids)),
                        counterpart_hash(c.counterpart_ids),
                        json.dumps(cp_inv),
                        party,
                        c.tier,
                        c.base_tier,
                        float(c.score),
                        c.suppressed_by,
                        ",".join(c.downgraded_by) or None,
                        json.dumps(ev, default=str),
                        int(ev["amount_at_risk_cents"]),
                        res.run_id,
                        ts,
                    )
                )
            subjects = sorted({r[5] for r in rows})
            before: set[str] = set()
            for i in range(0, len(subjects), 900):
                chunk = subjects[i : i + 900]
                before.update(
                    r["k"]
                    for r in conn.execute(
                        f"SELECT {_KEY} AS k FROM flags WHERE subject_id IN ({','.join('?' * len(chunk))})", chunk
                    ).fetchall()
                )
            conn.executemany(_UPSERT, rows)
            keys = {f"{r[0]}|{r[4]}|{r[5]}|{r[8]}" for r in rows}
            for i in range(0, len(subjects), 900):
                chunk = subjects[i : i + 900]
                for r in conn.execute(
                    f"SELECT id, {_KEY} AS k FROM flags WHERE last_run_id=? AND subject_id IN ({','.join('?' * len(chunk))})",
                    [res.run_id, *chunk],
                ).fetchall():
                    if r["k"] in keys:
                        (upd_ids if r["k"] in before else new_ids).append(r["id"])
            del rows
        res.new_flag_ids = sorted(set(new_ids))
        res.updated_flag_ids = sorted(set(upd_ids) - set(new_ids))
        if res.mode == "FULL":
            cur = conn.execute(
                "UPDATE flags SET active=0, updated_at=? WHERE status='OPEN' AND active=1"
                " AND (last_run_id IS NULL OR last_run_id<>?)",
                (ts, res.run_id),
            )
            res.deactivated = cur.rowcount


def _enqueue_ai(eng: Engine, res: DetectionResult) -> None:
    """Queue EXPLAIN / TRIAGE jobs for new flags when AI is enabled. Never blocks detection."""
    if eng.settings.get("ai.tier", "OFF") == "OFF" or not res.new_flag_ids:
        return
    from verismo_engine.ai.jobs import PRIORITY

    auto_explain = eng.settings.get("ai.auto_explain", True)
    auto_triage = eng.settings.get("ai.auto_triage", True)
    ph = ",".join("?" * len(res.new_flag_ids))
    rows = eng.db.query(
        f"SELECT id, tier FROM flags WHERE id IN ({ph}) AND suppressed_by IS NULL"
        " ORDER BY CASE tier WHEN 'HARD' THEN 0 WHEN 'PROBABLE' THEN 1 ELSE 2 END, amount_at_risk_cents DESC",
        res.new_flag_ids,
    )
    # Background AI work is bounded per run (largest amounts first) so a big import cannot queue
    # days of CPU time; reviewers can still request Investigate on any flag.
    cap = int(eng.settings.get("ai.max_background_jobs_per_run", 200))
    n = 0
    for r in rows:
        if n >= cap:
            break
        if auto_explain and r["tier"] in ("HARD", "PROBABLE"):
            eng.jobs.enqueue("AI_EXPLAIN", {"flag_id": r["id"]}, PRIORITY["BACKGROUND_EXPLAIN"])
            n += 1
        if auto_triage and r["tier"] in ("WEAK", "PROBABLE"):
            eng.jobs.enqueue("AI_TRIAGE", {"flag_id": r["id"]}, PRIORITY["BACKGROUND_TRIAGE"])
            n += 1


def preview_impact(eng: Engine, settings_override: dict[str, Any]) -> dict[str, Any]:
    """How many flags a settings change would add or remove, without applying it."""
    cur = detect(eng, persist=False)
    new = detect(eng, persist=False, settings_override=settings_override)

    def keys(r: DetectionResult) -> dict[tuple[Any, ...], str]:
        return {c.key(): c.tier for c in r.candidates if not c.suppressed_by}

    a, b = keys(cur), keys(new)
    added = [k for k in b if k not in a]
    removed = [k for k in a if k not in b]
    changed = [k for k in b if k in a and a[k] != b[k]]

    def by_rule(ks: list[tuple[Any, ...]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for k in ks:
            out[k[0]] = out.get(k[0], 0) + 1
        return out

    return {
        "current": len(a),
        "proposed": len(b),
        "added": len(added),
        "removed": len(removed),
        "tier_changed": len(changed),
        "added_by_rule": by_rule(added),
        "removed_by_rule": by_rule(removed),
    }


def review_pair_key(flag: dict[str, Any]) -> str:
    return pair_key(flag["subject_type"], [flag["subject_id"], *json.loads(flag["counterpart_ids"])])
