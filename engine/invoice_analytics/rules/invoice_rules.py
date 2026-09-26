"""Invoice-level rules INV-001 .. INV-008 (AP and AR)."""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from invoice_analytics.normalize import format_cents
from invoice_analytics.rules.base import (
    INV_ORDER,
    INV_SCOPE,
    LINE_ORDER,
    PATS_CTE,
    PATS_JOIN,
    PATS_OK,
    Candidate,
    Rule,
    RuleContext,
    downgrade,
    escalate,
)


def _scope(ctx: RuleContext, frag: str = INV_SCOPE) -> str:
    return frag if ctx.incremental else "TRUE"


_LIVE = "a.status <> 'VOID' AND b.status <> 'VOID'"


class INV001(Rule):
    rule_id = "INV-001"
    subject_type = "INVOICE"
    default_tier = "HARD"
    base_score = 0.97
    title = "Same party and same invoice number"
    summary_template = "Invoice number {invoice_number} from {party} was already recorded on invoice {other_invoice}."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, b.num_norm FROM inv a JOIN inv b
              ON a.cluster = b.cluster AND a.direction = b.direction AND a.num_norm = b.num_norm
            WHERE {INV_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.num_norm IS NOT NULL
              AND a.id <> b.id""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "INVOICE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={"invoice_number": "exact", "party": "cluster"},
            )
            for r in rows
        ]


class INV002(Rule):
    rule_id = "INV-002"
    subject_type = "INVOICE"
    default_tier = "HARD"
    base_score = 0.99
    title = "Same source file ingested twice"
    summary_template = "The same file (sha256 match) was already ingested as invoice {other_invoice}."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        # Pair invoices from different documents with identical file hashes, matched positionally
        # by invoice number within the file (a re-ingested file yields the same invoices again).
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other FROM inv a JOIN inv b
              ON a.doc_sha = b.doc_sha AND a.doc_id <> b.doc_id
             AND COALESCE(a.num_norm,'') = COALESCE(b.num_norm,'') AND a.total = b.total
            WHERE {INV_ORDER.replace('sort_key', 'doc_id')} AND {_scope(ctx)} AND a.doc_sha IS NOT NULL""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "INVOICE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={"document_sha256": "exact"},
            )
            for r in rows
        ]


class INV003(Rule):
    rule_id = "INV-003"
    subject_type = "INVOICE"
    default_tier = "PROBABLE"
    base_score = 0.75
    title = "Same party, total and invoice date; different invoice number"
    summary_template = (
        "{party} billed the same total {total} on the same date as invoice {other_invoice}, under a different number."
    )

    def run(self, ctx: RuleContext) -> list[Candidate]:
        rows = ctx.q(f"""
            WITH {PATS_CTE}
            SELECT b.id AS subject, a.id AS other FROM inv a JOIN inv b
              ON a.cluster = b.cluster AND a.direction = b.direction AND a.total = b.total
             AND a.inv_date = b.inv_date
            {PATS_JOIN}
            WHERE {INV_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.total <> 0 AND {PATS_OK}
              AND COALESCE(a.num_norm,'') <> COALESCE(b.num_norm,'')""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "INVOICE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={"total": "exact", "invoice_date": "exact"},
            )
            for r in rows
        ]


class INV004(Rule):
    rule_id = "INV-004"
    subject_type = "INVOICE"
    default_tier = "PROBABLE"
    base_score = 0.7
    title = "Near-identical invoice number (OCR/typo) with same amount"
    summary_template = "Invoice number {invoice_number} is within one character of {other_number} on invoice {other_invoice}, for the same amount."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        cfg = ctx.config
        tol = int(cfg.get("amount_tolerance_cents", 5))
        max_dl = int(cfg.get("max_damerau", 1))
        min_jw = float(cfg.get("min_jaro_winkler", 0.92))
        # JW >= 0.92 alone holds for most long numbers sharing a prefix; cap the edit distance too.
        jw_dl = int(cfg.get("max_damerau_with_jaro_winkler", 2))
        rows = ctx.q(f"""
            WITH {PATS_CTE}
            SELECT b.id AS subject, a.id AS other,
                   damerau_levenshtein(a.num_ocr, b.num_ocr) AS dl,
                   jaro_winkler_similarity(a.num_ocr, b.num_ocr) AS jw,
                   a.num_norm AS a_num, b.num_norm AS b_num,
                   abs(date_diff('day', a.inv_date, b.inv_date)) AS date_gap
            FROM inv a JOIN inv b ON a.cluster = b.cluster AND a.direction = b.direction
             AND b.total BETWEEN a.total - {tol} AND a.total + {tol}
            {PATS_JOIN}
            WHERE {INV_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.total <> 0 AND {PATS_OK}
              AND a.num_norm IS NOT NULL AND b.num_norm IS NOT NULL AND a.num_norm <> b.num_norm
              AND (damerau_levenshtein(a.num_ocr, b.num_ocr) <= {max_dl}
                   OR (jaro_winkler_similarity(a.num_ocr, b.num_ocr) >= {min_jw}
                       AND damerau_levenshtein(a.num_ocr, b.num_ocr) <= {jw_dl}))""")
        t = self.tier(ctx)
        min_len = int(cfg.get("min_length", 4))
        seq_gap = int(cfg.get("sequential_gap", 3))
        date_gap_max = int(cfg.get("probable_max_date_gap_days", 3))
        out = []
        for r in rows:
            a_num, b_num = str(r["a_num"]), str(r["b_num"])
            # Short numbers are one edit apart by chance.
            if min(len(a_num), len(b_num)) < min_len:
                continue
            # Consecutive numeric invoice numbers for a recurring amount are normal billing.
            if a_num.isdigit() and b_num.isdigit() and abs(int(a_num) - int(b_num)) <= seq_gap:
                continue
            # A re-keyed or re-scanned copy folds to the same number or keeps (nearly) the same date.
            # A single differing character on invoices weeks apart is usually the next invoice in a
            # series, so it is reported one tier lower.
            gap = r["date_gap"]
            same_fold = int(r["dl"]) == 0
            tier = t if same_fold or (gap is not None and gap <= date_gap_max) else downgrade(t)
            score = max(0.5, min(0.95, 0.5 * float(r["jw"]) + (0.45 if r["dl"] <= max_dl else 0.2)))
            out.append(
                Candidate(
                    self.rule_id,
                    "INVOICE",
                    r["subject"],
                    (r["other"],),
                    tier,
                    round(score, 4),
                    methods={
                        "invoice_number": {
                            "method": "damerau_ocr_folded",
                            "value": int(r["dl"]),
                            "jaro_winkler": round(float(r["jw"]), 4),
                        },
                        "total": {"method": "within_cents", "value": tol},
                    },
                    summary_params={"other_number": r["a_num"]},
                    extra={"date_gap_days": gap, "folded_equal": same_fold},
                )
            )
        return out


class INV005(Rule):
    rule_id = "INV-005"
    subject_type = "INVOICE"
    default_tier = "WEAK"
    base_score = 0.35
    title = "Same party and same total within N days"
    summary_template = "{party} billed the same total {total} within {window_days} days of invoice {other_invoice}."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        days = int(ctx.config.get("window_days", 45))
        rows = ctx.q(f"""
            WITH {PATS_CTE}
            SELECT b.id AS subject, a.id AS other, date_diff('day', a.inv_date, b.inv_date) AS gap
            FROM inv a JOIN inv b ON a.cluster = b.cluster AND a.direction = b.direction AND a.total = b.total
            {PATS_JOIN}
            WHERE {INV_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.total <> 0 AND {PATS_OK}
              AND a.inv_date IS NOT NULL AND b.inv_date IS NOT NULL
              AND b.inv_date > a.inv_date AND date_diff('day', a.inv_date, b.inv_date) <= {days}
              AND COALESCE(a.num_norm,'') <> COALESCE(b.num_norm,'')""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "INVOICE",
                r["subject"],
                (r["other"],),
                t,
                round(self.base_score + 0.2 * (1 - r["gap"] / max(1, days)), 4),
                methods={"total": "exact", "invoice_date": {"method": "days_apart", "value": r["gap"]}},
                summary_params={"window_days": days},
            )
            for r in rows
        ]


class INV006(Rule):
    rule_id = "INV-006"
    subject_type = "INVOICE"
    default_tier = "PROBABLE"
    base_score = 0.8
    title = "Line-set overlap: rebill under a new number"
    summary_template = "{overlap_pct}% of the lines on this invoice ({matched} of {line_count}) were already billed on invoice {other_invoice}."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        min_ov = float(ctx.config.get("min_overlap", 0.8))
        scope = (
            "(a.invoice_id IN (SELECT id FROM scope) OR b.invoice_id IN (SELECT id FROM scope))"
            if ctx.incremental
            else "TRUE"
        )
        rows = ctx.q(f"""
            WITH m AS (
              SELECT b.invoice_id AS b_inv, a.invoice_id AS a_inv, COUNT(DISTINCT b.id) AS matched
              FROM lines a JOIN lines b
                ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.code = b.code
               AND a.units = b.units AND a.charge = b.charge AND a.party_cluster = b.party_cluster
               AND a.invoice_id <> b.invoice_id
              WHERE {LINE_ORDER} AND a.inv_status <> 'VOID' AND b.inv_status <> 'VOID' AND {scope}
                AND a.patient_cluster IS NOT NULL AND a.code IS NOT NULL
              GROUP BY 1, 2)
            SELECT m.b_inv AS subject, m.a_inv AS other, m.matched, bi.line_count
            FROM m JOIN inv bi ON bi.id = m.b_inv JOIN inv ai ON ai.id = m.a_inv
            WHERE bi.line_count > 0 AND m.matched * 1.0 / bi.line_count >= {min_ov}
              AND COALESCE(ai.num_norm,'') <> COALESCE(bi.num_norm,'')""")
        base = self.tier(ctx)
        out = []
        for r in rows:
            ratio = r["matched"] / r["line_count"]
            tier = "HARD" if ratio >= 1.0 else base
            out.append(
                Candidate(
                    self.rule_id,
                    "INVOICE",
                    r["subject"],
                    (r["other"],),
                    tier,
                    round(0.6 + 0.35 * ratio, 4),
                    methods={"line_set": {"method": "overlap", "value": round(ratio, 4)}},
                    summary_params={
                        "overlap_pct": int(round(ratio * 100)),
                        "matched": r["matched"],
                        "line_count": r["line_count"],
                    },
                    extra={"overlap": ratio},
                )
            )
        return out


class INV007(Rule):
    rule_id = "INV-007"
    subject_type = "INVOICE"
    default_tier = "PROBABLE"
    base_score = 0.85
    title = "Duplicate across party records that share a tax ID, NPI or remit address"
    summary_template = "Separate party records share a {shared_identifier}, and invoice {other_invoice} matches this one; escalated one tier."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        # One equi-join per shared identifier (an OR join would be a nested loop over all pairs).
        shared = []
        for col, label in (("tax_h", "tax ID"), ("party_npi", "NPI"), ("addr", "remit address")):
            shared.append(f"""
            SELECT b.id AS subject, a.id AS other, '{label}' AS shared,
                   (a.num_norm IS NOT NULL AND a.num_norm = b.num_norm) AS same_num,
                   (a.total = b.total AND a.inv_date = b.inv_date AND a.total <> 0) AS same_amt_date
            FROM inv a JOIN inv b ON a.{col} = b.{col} AND a.cluster <> b.cluster AND a.direction = b.direction
            WHERE a.{col} IS NOT NULL AND {INV_ORDER} AND {_LIVE} AND {_scope(ctx)}
              AND ((a.num_norm IS NOT NULL AND a.num_norm = b.num_norm)
                OR (a.total = b.total AND a.inv_date = b.inv_date AND a.total <> 0))""")
        rows = ctx.q(f"""
            SELECT subject, other, MIN(shared) AS shared, BOOL_OR(same_num) AS same_num,
                   BOOL_OR(same_amt_date) AS same_amt_date
            FROM ({" UNION ALL ".join(shared)}) GROUP BY subject, other""")
        out = []
        for r in rows:
            base = "HARD" if r["same_num"] else "PROBABLE"  # INV-001 or INV-003 equivalent
            tier = escalate(base)
            methods: dict[str, Any] = {"party": {"method": f"shared_{r['shared'].replace(' ', '_').lower()}"}}
            if r["same_num"]:
                methods["invoice_number"] = "exact"
            if r["same_amt_date"]:
                methods["total"] = "exact"
                methods["invoice_date"] = "exact"
            out.append(
                Candidate(
                    self.rule_id,
                    "INVOICE",
                    r["subject"],
                    (r["other"],),
                    tier,
                    0.9 if r["same_num"] else 0.8,
                    methods=methods,
                    summary_params={"shared_identifier": r["shared"]},
                    extra={"escalated_from": base},
                )
            )
        return out


class INV008(Rule):
    rule_id = "INV-008"
    subject_type = "INVOICE"
    default_tier = "WEAK"
    base_score = 0.4
    title = "Split billing: several invoices summing to a prior invoice total"
    summary_template = "{parts} invoices from {party} within {window_days} days sum to {total_sum}, the total of prior invoice {other_invoice}."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        cfg = ctx.config
        window = int(cfg.get("window_days", 30))
        tol = int(cfg.get("tolerance_cents", 100))
        max_parts = max(2, min(4, int(cfg.get("max_parts", 4))))
        cap = int(cfg.get("max_candidates", 20))
        # candidate parts: later invoices from same party within window, strictly smaller than P
        # Per party cluster, invoices sorted by date: for each prior invoice P take the next invoices
        # within the window that are smaller than P (at most `cap`). Linear memory, no pair explosion.
        rows = ctx.qnp("""
            SELECT id, cluster, direction, total, sort_key, epoch(inv_date) // 86400 AS day
            FROM inv WHERE total > 0 AND status <> 'VOID' AND inv_date IS NOT NULL
            ORDER BY cluster, direction, sort_key""")
        scope_ids: set[int] | None = None
        if ctx.incremental:
            scope_ids = {int(x) for x in ctx.con.execute("SELECT id FROM scope").fetchnumpy()["id"].tolist()}
        groups: dict[int, list[tuple[int, int]]] = {}
        ptot: dict[int, int] = {}
        combos_cache: dict[tuple[int, int], np.ndarray] = {}
        out: list[Candidate] = []
        ids_a = rows["id"].astype(np.int64)
        tot_a = rows["total"].astype(np.int64)
        day_a = rows["day"].astype(np.int64)
        key = np.array([f"{c}|{d}" for c, d in zip(rows["cluster"].tolist(), rows["direction"].tolist(), strict=True)])
        bounds = np.flatnonzero(key[1:] != key[:-1]) + 1 if len(key) else np.array([], dtype=np.int64)
        for seg_start, seg_end in zip(np.r_[0, bounds], np.r_[bounds, len(key)], strict=True):
            seg_ids, seg_tot, seg_day = ids_a[seg_start:seg_end], tot_a[seg_start:seg_end], day_a[seg_start:seg_end]
            ends = np.searchsorted(seg_day, seg_day + window, side="right")
            for i in range(len(seg_ids)):
                lo, hi = i + 1, ends[i]
                if hi - lo < 2:
                    continue
                win_tot = seg_tot[lo:hi]
                sel = np.flatnonzero(win_tot < seg_tot[i])[:cap]
                if len(sel) < 2:
                    continue
                # Phase 1 (totals only, cheap): keep P only if some combination reaches its total.
                vals0 = win_tot[sel]
                if self._search(int(seg_tot[i]), vals0, tol, max_parts, combos_cache)[0] == 0:
                    continue
                pid_ = int(seg_ids[i])
                parts = [(int(seg_ids[lo + x]), int(win_tot[x])) for x in sel]
                if scope_ids is not None and pid_ not in scope_ids and not any(c in scope_ids for c, _ in parts):
                    continue
                groups[pid_] = parts
                ptot[pid_] = int(seg_tot[i])
                if len(groups) >= 5000:  # bounded memory: resolve phase 2 in chunks
                    out.extend(self._phase2(ctx, groups, ptot, tol, max_parts, combos_cache, window))
                    groups.clear()
                    ptot.clear()
        out.extend(self._phase2(ctx, groups, ptot, tol, max_parts, combos_cache, window))
        return out

    def _phase2(
        self,
        ctx: RuleContext,
        groups: dict[int, list[tuple[int, int]]],
        ptot: dict[int, int],
        tol: int,
        max_parts: int,
        combos_cache: dict[tuple[int, int], np.ndarray],
        window: int,
    ) -> list[Candidate]:
        """Parts must share content with P; the combination must then be unique."""
        t = self.tier(ctx)
        out: list[Candidate] = []
        if not groups:
            return out
        content = self._content(ctx, {i for pid, parts in groups.items() for i in (pid, *[p[0] for p in parts])})
        for pid, parts in groups.items():
            parts = [p for p in parts if self._related(content.get(pid), content.get(p[0]))]
            if len(parts) < 2:
                continue
            target = ptot[pid]
            ids = [p[0] for p in parts]
            vals = np.array([p[1] for p in parts], dtype=np.int64)
            hits, idx = self._search(target, vals, tol, max_parts, combos_cache)
            found = tuple(ids[i] for i in idx) if hits == 1 and idx is not None else None
            if found:
                total_sum = format_cents(int(sum(vals[ids.index(i)] for i in found)))
                # subject: the latest part; counterparts: the prior invoice + other parts
                subject = found[-1]
                out.append(
                    Candidate(
                        self.rule_id,
                        "INVOICE",
                        subject,
                        (pid, *found[:-1]),
                        t,
                        self.base_score,
                        methods={"total": {"method": "subset_sum", "value": len(found), "tolerance_cents": tol}},
                        summary_params={"parts": len(found), "window_days": window, "total_sum": total_sum},
                        extra={"prior_invoice": pid, "parts": list(found)},
                    )
                )
        return out

    @staticmethod
    def _search(
        target: int, vals: np.ndarray, tol: int, max_parts: int, cache: dict[tuple[int, int], np.ndarray]
    ) -> tuple[int, tuple[int, ...] | None]:
        """Smallest part count k with a combination reaching target; returns (hits, indices).
        hits > 1 means several combinations work (coincidence). Quadruples use the 12 earliest."""
        n = len(vals)
        for k in range(2, max_parts + 1):
            n_k = min(n, 12) if k == 4 else n
            if n_k < k:
                continue
            key = (n_k, k)
            if key not in cache:
                cache[key] = np.array(list(itertools.combinations(range(n_k), k)), dtype=np.int64)
            combos = cache[key]
            hit = np.flatnonzero(np.abs(vals[:n_k][combos].sum(axis=1) - target) <= tol)
            if hit.size:
                return int(hit.size), tuple(int(x) for x in combos[hit[0]])
        return 0, None

    @staticmethod
    def _content(ctx: RuleContext, invoice_ids: set[int]) -> dict[int, dict[str, Any]]:
        """Per invoice: (patient, code) pairs, description tokens and PO number."""
        if not invoice_ids:
            return {}
        import pyarrow as pa

        con = ctx.con
        con.register("_i8", pa.table({"id": pa.array(sorted(invoice_ids), pa.int64())}))
        try:
            out: dict[int, dict[str, Any]] = {i: {"pc": set(), "desc": set(), "po": None} for i in invoice_ids}
            for iid, pcl, code, desc in con.execute(
                "SELECT l.invoice_id, l.patient_cluster, l.code, l.desc_norm FROM lines l JOIN _i8 ON _i8.id = l.invoice_id"
            ).fetchall():
                if pcl is not None:
                    out[iid]["pc"].add((pcl, code))
                if desc:
                    out[iid]["desc"].update(desc.split())
            for iid, po in con.execute("SELECT i.id, i.po_number FROM inv i JOIN _i8 ON _i8.id = i.id").fetchall():
                out[iid]["po"] = po
            return out
        finally:
            con.unregister("_i8")

    @staticmethod
    def _related(p: dict[str, Any] | None, c: dict[str, Any] | None) -> bool:
        """A part must share content with the prior invoice: same PO, overlapping patient/code
        lines, or (header-only invoices) overlapping line descriptions."""
        if not p or not c:
            return False
        if p["po"] and c["po"]:
            return bool(p["po"] == c["po"])
        if p["pc"] or c["pc"]:
            return bool(c["pc"]) and len(c["pc"] & p["pc"]) / len(c["pc"]) >= 0.5
        if p["desc"] and c["desc"]:
            return len(p["desc"] & c["desc"]) / len(c["desc"]) >= 0.6
        return False


INVOICE_RULES: list[Rule] = [INV001(), INV002(), INV003(), INV004(), INV005(), INV006(), INV007(), INV008()]
