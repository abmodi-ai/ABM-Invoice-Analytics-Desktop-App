"""Line-level clinical rules CLN-001 .. CLN-009.

Reference lookups are effective-dated by the line's date of service, never by today's date.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from invoice_analytics.rules.base import LINE_ORDER, LINE_SCOPE, Candidate, Rule, RuleContext

GLOBAL_BYPASS_MODS = ("24", "25", "57", "58", "78", "79")


def _scope(ctx: RuleContext) -> str:
    return LINE_SCOPE if ctx.incremental else "TRUE"


def _sql_list(items: list[str] | tuple[str, ...] | set[str]) -> str:
    vals = ",".join("'" + i.replace("'", "") + "'" for i in sorted(items))
    return f"[{vals}]::VARCHAR[]" if vals else "[]::VARCHAR[]"


def modifier_sets(ctx: RuleContext) -> dict[str, set[str]]:
    rows = ctx.q("SELECT modifier, category, ncci_bypass FROM ref_modifiers")
    out: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        out[r["category"]].add(r["modifier"])
        if int(r["ncci_bypass"] or 0):
            out["NCCI_BYPASS"].add(r["modifier"])
    return out


def _mods(col: str) -> str:
    return f"string_split(NULLIF({col}, ''), ',')"


_LIVE = "a.inv_status <> 'VOID' AND b.inv_status <> 'VOID'"
_SETTING = "CASE WHEN {t}.claim_type = 'INSTITUTIONAL' THEN 'HOSPITAL' ELSE 'PRACTITIONER' END"


class CLN001(Rule):
    rule_id = "CLN-001"
    subject_type = "LINE"
    default_tier = "HARD"
    base_score = 0.97
    title = "Exact duplicate line across invoices"
    summary_template = (
        "Same patient, date of service, code {code}, modifiers and units already billed on invoice {other_invoice}."
    )

    def run(self, ctx: RuleContext) -> list[Candidate]:
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.code = b.code
             AND a.mods = b.mods AND a.units = b.units
             AND COALESCE(a.npi,'') = COALESCE(b.npi,'') AND a.invoice_id <> b.invoice_id
            WHERE {LINE_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.patient_cluster IS NOT NULL
              AND a.code IS NOT NULL AND a.dos IS NOT NULL""")
        t = self.tier(ctx)
        m = {
            "patient_cluster": "exact",
            "dos": "exact",
            "code": "exact",
            "modifiers": "exact",
            "units": "exact",
            "rendering_npi": "exact",
        }
        return [
            Candidate(self.rule_id, "LINE", r["subject"], (r["other"],), t, self.base_score, methods=m) for r in rows
        ]


class CLN002(Rule):
    rule_id = "CLN-002"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.7
    title = "Same patient, DOS and code with different modifiers (no distinct/repeat/bilateral modifier)"
    summary_template = "Code {code} was billed twice for the same patient and date with different modifiers ({mods_a} vs {mods_b}), and neither line carries a distinct, repeat or bilateral modifier."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        ms = modifier_sets(ctx)
        allowed = ms["DISTINCT"] | ms["REPEAT"] | ms["BILATERAL"]
        lst = _sql_list(allowed)
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, a.mods AS ma, b.mods AS mb FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.code = b.code
             AND a.mods <> b.mods AND a.id <> b.id
            WHERE {LINE_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.patient_cluster IS NOT NULL
              AND a.code IS NOT NULL
              AND NOT list_has_any(COALESCE({_mods('a.mods')}, []::VARCHAR[]), {lst})
              AND NOT list_has_any(COALESCE({_mods('b.mods')}, []::VARCHAR[]), {lst})""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "LINE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={"patient_cluster": "exact", "dos": "exact", "code": "exact"},
                summary_params={"mods_a": r["ma"] or "none", "mods_b": r["mb"] or "none"},
            )
            for r in rows
        ]


class CLN003(Rule):
    rule_id = "CLN-003"
    subject_type = "LINE"
    default_tier = "WEAK"
    base_score = 0.35
    title = "Same patient and code within one day by a different rendering provider in the same party"
    summary_template = (
        "Code {code} was billed for the same patient by two different rendering providers within {day_window} day(s)."
    )

    def run(self, ctx: RuleContext) -> list[Candidate]:
        w = int(ctx.config.get("day_window", 1))
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, abs(date_diff('day', a.dos, b.dos)) AS gap
            FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.code = b.code AND a.party_cluster = b.party_cluster
             AND b.dos BETWEEN a.dos - INTERVAL {w} DAY AND a.dos + INTERVAL {w} DAY
             AND a.npi IS NOT NULL AND b.npi IS NOT NULL AND a.npi <> b.npi AND a.id <> b.id
            WHERE {LINE_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.patient_cluster IS NOT NULL""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "LINE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={
                    "patient_cluster": "exact",
                    "code": "exact",
                    "dos": {"method": "days_apart", "value": int(r["gap"])},
                },
                summary_params={"day_window": w},
                extra={"interval_days": int(r["gap"])},
            )
            for r in rows
        ]


class CLN004(Rule):
    rule_id = "CLN-004"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.75
    title = "MUE exceeded"
    summary_template = "{units_total} units of {code} on {dos} exceed the MUE of {mue} (MAI {mai})."
    uses_refdata = ("MUE",)

    def run(self, ctx: RuleContext) -> list[Candidate]:
        base = self.tier(ctx)
        scope_l = "l.invoice_id IN (SELECT id FROM scope)" if ctx.incremental else "TRUE"
        joined = f"""
            SELECT l.*, m.mue_value, m.mai, m.version AS mue_version, m.setting AS mue_setting,
                   ({scope_l}) AS in_sc
            FROM lines l JOIN mue m ON m.code = l.code AND m.setting = {_SETTING.format(t='l')}
             AND l.dos >= m.eff_from AND (m.eff_to IS NULL OR l.dos <= m.eff_to)
            WHERE l.inv_status <> 'VOID' AND l.patient_cluster IS NOT NULL AND l.dos IS NOT NULL"""
        out: list[Candidate] = []
        # MAI 1: per claim line
        for r in ctx.q(f"SELECT * FROM ({joined}) l WHERE l.mai = 1 AND l.units > l.mue_value AND {scope_l}"):
            out.append(self._cand(r, (), base, r["units"]))
        # MAI 2/3: per date of service across claims
        groups = ctx.q(f"""
            WITH j AS ({joined} AND m.mai IN (2, 3))
            SELECT patient_cluster, code, dos, ANY_VALUE(mue_value) AS mue_value, ANY_VALUE(mai) AS mai,
                   ANY_VALUE(mue_version) AS mue_version, ANY_VALUE(mue_setting) AS mue_setting,
                   SUM(units) AS units_total, list(id ORDER BY inv_sort, id)[-1] AS subject,
                   list(id ORDER BY inv_sort, id) AS ids, bool_or(in_sc) AS in_scope
            FROM j l GROUP BY patient_cluster, code, dos HAVING SUM(units) > ANY_VALUE(mue_value)""")
        for g in groups:
            if not g["in_scope"]:
                continue
            others = tuple(i for i in g["ids"] if i != g["subject"])
            tier = "HARD" if int(g["mai"]) == 2 else base
            out.append(self._cand({**g, "id": g["subject"]}, others, tier, g["units_total"]))
        return out

    def _cand(self, r: dict[str, Any], others: tuple[int, ...], tier: str, units_total: float) -> Candidate:
        return Candidate(
            self.rule_id,
            "LINE",
            int(r["id"]),
            others,
            tier,
            self.base_score,
            methods={"units": {"method": "sum_vs_mue", "value": float(units_total)}},
            refdata=[
                {
                    "dataset": "MUE",
                    "version": r["mue_version"],
                    "row": {
                        "code": r["code"],
                        "mue": int(r["mue_value"]),
                        "mai": int(r["mai"]),
                        "setting": r["mue_setting"],
                    },
                }
            ],
            summary_params={
                "units_total": _num(units_total),
                "mue": int(r["mue_value"]),
                "mai": int(r["mai"]),
                "dos": str(r["dos"]),
            },
            extra={"units_total": float(units_total), "mue": int(r["mue_value"]), "mai": int(r["mai"])},
        )


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


class CLN005(Rule):
    rule_id = "CLN-005"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.7
    title = "NCCI procedure-to-procedure pair billed together"
    summary_template = "{code_col2} is a column-2 code bundled into {code_col1} (NCCI modifier indicator {mi}) for the same patient, date and provider."
    uses_refdata = ("NCCI_PTP",)

    def run(self, ctx: RuleContext) -> list[Candidate]:
        ms = modifier_sets(ctx)
        bypass = _sql_list(ms["NCCI_BYPASS"])
        base = self.tier(ctx)
        scope = (
            "(a.invoice_id IN (SELECT id FROM scope) OR b.invoice_id IN (SELECT id FROM scope))"
            if ctx.incremental
            else "TRUE"
        )
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, a.code AS c1, b.code AS c2, p.mi,
                   list_has_any(COALESCE({_mods('b.mods')}, []::VARCHAR[]), {bypass}) AS b_bypass,
                   list_has_any(COALESCE({_mods('a.mods')}, []::VARCHAR[]), {bypass}) AS a_bypass,
                   {_SETTING.format(t='b')} AS setting
            FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.id <> b.id
             AND COALESCE(a.npi, CAST(a.party_cluster AS VARCHAR)) = COALESCE(b.npi, CAST(b.party_cluster AS VARCHAR))
            JOIN ptp p ON p.c1 = a.code AND p.c2 = b.code AND p.setting = {_SETTING.format(t='b')}
             AND b.dos >= p.eff_from AND (p.eff_to IS NULL OR b.dos <= p.eff_to)
            WHERE {_LIVE} AND {scope} AND a.patient_cluster IS NOT NULL AND p.mi IN ('0', '1')""")
        out = []
        for r in rows:
            if r["mi"] == "0":
                tier, why = base, "modifier indicator 0: never separately payable"
            elif not (r["b_bypass"] or r["a_bypass"]):
                tier, why = base, "modifier indicator 1 with no NCCI-associated modifier"
            else:
                tier, why = "INFO", "modifier indicator 1 with an NCCI bypass modifier"
            out.append(
                Candidate(
                    self.rule_id,
                    "LINE",
                    r["subject"],
                    (r["other"],),
                    tier,
                    self.base_score,
                    methods={
                        "patient_cluster": "exact",
                        "dos": "exact",
                        "rendering_npi": "exact",
                        "code": {"method": "ncci_ptp_pair", "value": f"{r['c1']}/{r['c2']}"},
                    },
                    refdata=[
                        {
                            "dataset": "NCCI_PTP",
                            "row": {
                                "column1": r["c1"],
                                "column2": r["c2"],
                                "modifier_indicator": r["mi"],
                                "setting": r["setting"],
                            },
                        }
                    ],
                    summary_params={"code_col1": r["c1"], "code_col2": r["c2"], "mi": r["mi"]},
                    extra={"reason": why},
                )
            )
        return out


class CLN006(Rule):
    rule_id = "CLN-006"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.65
    title = "Service inside a surgical global period"
    summary_template = "{code} on {dos} falls {days_after} days after surgery {surgery_code} ({global_days}-day global period) by the same provider, without modifier 24, 25, 57, 58, 78 or 79."
    uses_refdata = ("PFS_GLOBAL",)

    def run(self, ctx: RuleContext) -> list[Candidate]:
        base = self.tier(ctx)
        scope = (
            "(s.invoice_id IN (SELECT id FROM scope) OR b.invoice_id IN (SELECT id FROM scope))"
            if ctx.incremental
            else "TRUE"
        )
        mods = _sql_list(GLOBAL_BYPASS_MODS)
        rows = ctx.q(f"""
            SELECT b.id AS subject, s.id AS surgery, s.code AS s_code, b.code AS b_code, g.global_days,
                   date_diff('day', s.dos, b.dos) AS days_after, b.dos AS b_dos
            FROM lines s
            JOIN ref_global_days g ON g.code = s.code AND g.effective_year = year(s.dos)
             AND g.global_days IN ('010', '090')
            JOIN lines b ON b.patient_cluster = s.patient_cluster AND b.npi = s.npi AND b.id <> s.id
             AND b.dos > s.dos AND b.dos <= s.dos + CAST(CAST(g.global_days AS INTEGER) AS INTEGER) * INTERVAL 1 DAY
            WHERE s.inv_status <> 'VOID' AND b.inv_status <> 'VOID' AND {scope}
              AND s.patient_cluster IS NOT NULL AND s.npi IS NOT NULL
              AND NOT list_has_any(COALESCE({_mods('b.mods')}, []::VARCHAR[]), {mods})""")
        return [
            Candidate(
                self.rule_id,
                "LINE",
                r["subject"],
                (r["surgery"],),
                base,
                self.base_score,
                methods={
                    "patient_cluster": "exact",
                    "rendering_npi": "exact",
                    "dos": {"method": "days_after_surgery", "value": int(r["days_after"])},
                },
                refdata=[{"dataset": "PFS_GLOBAL", "row": {"code": r["s_code"], "global_days": r["global_days"]}}],
                summary_params={
                    "surgery_code": r["s_code"],
                    "global_days": int(r["global_days"]),
                    "days_after": int(r["days_after"]),
                    "dos": str(r["b_dos"]),
                },
            )
            for r in rows
        ]


class CLN007(Rule):
    rule_id = "CLN-007"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.6
    title = "Frequency limit exceeded"
    summary_template = "{code} was billed {count} times in the {period} period; the limit is {max_count}."
    uses_refdata = ("FREQUENCY_LIMITS",)

    _BUCKET = {
        "DAY": "strftime(l.dos, '%Y-%m-%d')",
        "WEEK": "strftime(l.dos, '%G-W%V')",
        "MONTH": "strftime(l.dos, '%Y-%m')",
        "YEAR": "strftime(l.dos, '%Y')",
        "LIFETIME": "'lifetime'",
    }

    def run(self, ctx: RuleContext) -> list[Candidate]:
        base = self.tier(ctx)
        limits = ctx.q("SELECT id, code, max_count, period, scope FROM ref_frequency_limits")
        out: list[Candidate] = []
        for lim in limits:
            bucket = self._BUCKET.get(lim["period"])
            if bucket is None:
                continue
            prov = ", l.npi" if lim["scope"] == "PATIENT_PROVIDER" else ""
            scope_l = "invoice_id IN (SELECT id FROM scope)" if ctx.incremental else "TRUE"
            rows = ctx.q(
                f"""
                WITH w AS (
                  SELECT l.id, l.invoice_id, l.dos, l.units, {bucket} AS bucket,
                         SUM(l.units) OVER (PARTITION BY l.patient_cluster{prov}, {bucket}
                                            ORDER BY l.dos, l.inv_sort, l.id
                                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running,
                         list(l.id) OVER (PARTITION BY l.patient_cluster{prov}, {bucket}
                                          ORDER BY l.dos, l.inv_sort, l.id
                                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_ids
                  FROM lines l WHERE l.code = ? AND l.patient_cluster IS NOT NULL AND l.dos IS NOT NULL
                    AND l.inv_status <> 'VOID')
                SELECT * FROM w WHERE running > ? AND {scope_l}""",
                [lim["code"], lim["max_count"]],
            )
            for r in rows:
                prior = tuple((r["prior_ids"] or [])[-10:])
                out.append(
                    Candidate(
                        self.rule_id,
                        "LINE",
                        r["id"],
                        prior,
                        base,
                        self.base_score,
                        methods={"units": {"method": "running_count", "value": float(r["running"])}},
                        refdata=[
                            {
                                "dataset": "FREQUENCY_LIMITS",
                                "row": {
                                    "code": lim["code"],
                                    "max_count": lim["max_count"],
                                    "period": lim["period"],
                                    "scope": lim["scope"],
                                },
                            }
                        ],
                        summary_params={
                            "count": _num(r["running"]),
                            "period": lim["period"].lower(),
                            "max_count": lim["max_count"],
                        },
                        extra={"limit_id": lim["id"], "bucket": r["bucket"]},
                    )
                )
        return out


class CLN008(Rule):
    rule_id = "CLN-008"
    subject_type = "LINE"
    default_tier = "WEAK"
    base_score = 0.4
    title = "Semantic duplicate description"
    summary_template = "Descriptions for the same patient and date are semantically near-identical (cosine {cosine}) although codes differ or are missing."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        from invoice_analytics.normalize.embeddings import get_embedder

        thr = float(ctx.config.get("min_cosine", 0.90))
        pairs = ctx.qnp(f"""
            SELECT b.id AS subject, a.id AS other FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.id <> b.id
             AND a.has_emb AND b.has_emb
             AND (a.code IS NULL OR b.code IS NULL OR a.code <> b.code)
            WHERE {LINE_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.patient_cluster IS NOT NULL""")
        subj_all, other_all = pairs["subject"], pairs["other"]
        if len(subj_all) == 0:
            return []
        embedder_id = get_embedder(ctx.store.eng.config.models_dir).embedder_id
        t = self.tier(ctx)
        out = []
        # Embeddings come from the encrypted DB in bounded chunks (never all lines at once).
        for start in range(0, len(subj_all), 20_000):
            subj = subj_all[start : start + 20_000].tolist()
            other = other_all[start : start + 20_000].tolist()
            got, mat = ctx.store.embeddings_for(subj + other)
            pos = {lid: i for i, lid in enumerate(got)}
            a_idx = np.array([pos.get(o, -1) for o in other])
            b_idx = np.array([pos.get(sb, -1) for sb in subj])
            ok = (a_idx >= 0) & (b_idx >= 0)
            cos = np.full(len(subj), -1.0, dtype=np.float32)
            if ok.any():
                cos[ok] = np.einsum("ij,ij->i", mat[a_idx[ok]], mat[b_idx[ok]])
            for sb, ot, c in zip(subj, other, cos.tolist(), strict=True):
                if c >= thr:
                    out.append(
                        Candidate(
                            self.rule_id,
                            "LINE",
                            int(sb),
                            (int(ot),),
                            t,
                            round(float(c) * 0.5, 4),
                            methods={
                                "patient_cluster": "exact",
                                "dos": "exact",
                                "description": {
                                    "method": "embedding_cosine",
                                    "value": round(float(c), 4),
                                    "embedder": embedder_id,
                                },
                            },
                            summary_params={"cosine": f"{float(c):.2f}"},
                        )
                    )
            del mat, got, pos
        return out


class CLN009(Rule):
    rule_id = "CLN-009"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.7
    title = "Rebill without correction (frequency code not 7 or 8)"
    summary_template = "This line matches a line already billed on invoice {other_invoice}, but the new claim is not marked as a replacement (7) or void (8)."

    def run(self, ctx: RuleContext) -> list[Candidate]:
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, b.freq AS bfreq, a.inv_status AS a_status,
                   COALESCE(a.paid, 0) AS a_paid
            FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.code = b.code
             AND a.units = b.units AND a.party_cluster = b.party_cluster AND a.invoice_id <> b.invoice_id
            WHERE {LINE_ORDER} AND {_LIVE} AND {_scope(ctx)} AND a.patient_cluster IS NOT NULL
              AND a.code IS NOT NULL AND COALESCE(b.freq, '') NOT IN ('7', '8')
              AND (b.freq IS NOT NULL OR a.inv_status = 'PAID' OR COALESCE(a.paid, 0) > 0)""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "LINE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={
                    "patient_cluster": "exact",
                    "dos": "exact",
                    "code": "exact",
                    "units": "exact",
                    "claim_frequency_code": {"method": "not_7_or_8", "value": r["bfreq"]},
                },
                extra={"prior_paid": bool(r["a_paid"] > 0 or r["a_status"] == "PAID")},
            )
            for r in rows
        ]


class CLN010(Rule):
    rule_id = "CLN-010"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.8
    title = "Same charge twice on one invoice"
    summary_template = (
        "{code} for the same patient/subject on {dos} appears twice on this invoice for the same amount" "{visit_note}."
    )

    def run(self, ctx: RuleContext) -> list[Candidate]:
        ms = modifier_sets(ctx)
        allowed = _sql_list(ms["DISTINCT"] | ms["REPEAT"] | ms["BILATERAL"])
        scope = "b.invoice_id IN (SELECT id FROM scope)" if ctx.incremental else "TRUE"
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, a.visit AS va, b.visit AS vb FROM lines a JOIN lines b
              ON a.invoice_id = b.invoice_id AND a.patient_cluster = b.patient_cluster AND a.dos = b.dos
             AND a.code = b.code AND a.charge = b.charge AND a.units = b.units AND a.mods = b.mods AND a.id < b.id
            WHERE a.inv_status <> 'VOID' AND {scope} AND a.patient_cluster IS NOT NULL AND a.code IS NOT NULL
              AND a.charge > 0
              AND NOT list_has_any(COALESCE({_mods('b.mods')}, []::VARCHAR[]), {allowed})""")
        t = self.tier(ctx)
        out = []
        for r in rows:
            note = f" (billed as visits {r['va']} and {r['vb']})" if r["va"] and r["vb"] and r["va"] != r["vb"] else ""
            out.append(
                Candidate(
                    self.rule_id,
                    "LINE",
                    r["subject"],
                    (r["other"],),
                    t,
                    self.base_score,
                    methods={"patient_cluster": "exact", "dos": "exact", "code": "exact", "charge": "exact"},
                    summary_params={"visit_note": note},
                )
            )
        return out


class CLN011(Rule):
    rule_id = "CLN-011"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.6
    title = "High-cost service repeated for the same patient"
    summary_template = (
        "{code} ({charge}) was billed again for the same patient/subject {days_apart} days after an identical charge on "
        "{other_dos}; confirm the repeat was ordered and approved."
    )

    def run(self, ctx: RuleContext) -> list[Candidate]:
        cfg = ctx.config
        window = int(cfg.get("window_days", 30))
        min_charge = int(cfg.get("min_charge_cents", 100_000))
        rows = ctx.q(f"""
            SELECT b.id AS subject, a.id AS other, date_diff('day', a.dos, b.dos) AS gap, a.dos AS a_dos, b.charge
            FROM lines a JOIN lines b
              ON a.patient_cluster = b.patient_cluster AND a.code = b.code AND a.charge = b.charge
             AND b.dos > a.dos AND b.dos <= a.dos + INTERVAL {window} DAY AND a.id <> b.id
            WHERE {_LIVE} AND {_scope(ctx)} AND a.patient_cluster IS NOT NULL AND a.code IS NOT NULL
              AND a.charge >= {min_charge}""")
        t = self.tier(ctx)
        from invoice_analytics.normalize import format_cents

        return [
            Candidate(
                self.rule_id,
                "LINE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={
                    "patient_cluster": "exact",
                    "code": "exact",
                    "charge": "exact",
                    "dos": {"method": "days_apart", "value": int(r["gap"])},
                },
                summary_params={
                    "days_apart": int(r["gap"]),
                    "other_dos": str(r["a_dos"]),
                    "charge": format_cents(int(r["charge"])),
                },
                extra={"interval_days": int(r["gap"])},
            )
            for r in rows
        ]


_TIMEPOINT = r"'^(D-?[0-9]+|M[0-9]+(D[0-9]+)?)$'"


class CLN012(Rule):
    rule_id = "CLN-012"
    subject_type = "LINE"
    default_tier = "PROBABLE"
    base_score = 0.65
    title = "Two protocol visits billed on the same date"
    summary_template = (
        "Visits {visit_a} and {visit_b} for the same subject are both dated {dos}; different protocol timepoints "
        "cannot share a date, so one date is wrong or a visit was billed twice."
    )

    def run(self, ctx: RuleContext) -> list[Candidate]:
        scope = (
            "(a.invoice_id IN (SELECT id FROM scope) OR b.invoice_id IN (SELECT id FROM scope))"
            if ctx.incremental
            else "TRUE"
        )
        rows = ctx.q(f"""
            WITH v AS (
              SELECT patient_cluster, dos, visit, MIN(id) AS id, ANY_VALUE(invoice_id) AS invoice_id,
                     ANY_VALUE(inv_sort) AS inv_sort
              FROM lines WHERE visit IS NOT NULL AND regexp_matches(visit, {_TIMEPOINT})
                AND patient_cluster IS NOT NULL AND dos IS NOT NULL AND inv_status <> 'VOID'
              GROUP BY patient_cluster, dos, visit)
            SELECT b.id AS subject, a.id AS other, a.visit AS va, b.visit AS vb, a.dos
            FROM v a JOIN v b ON a.patient_cluster = b.patient_cluster AND a.dos = b.dos AND a.visit < b.visit
            WHERE {scope}""")
        t = self.tier(ctx)
        return [
            Candidate(
                self.rule_id,
                "LINE",
                r["subject"],
                (r["other"],),
                t,
                self.base_score,
                methods={
                    "patient_cluster": "exact",
                    "dos": "exact",
                    "visit": {"method": "different_timepoints", "value": f"{r['va']} / {r['vb']}"},
                },
                summary_params={"visit_a": r["va"], "visit_b": r["vb"], "dos": str(r["dos"])},
            )
            for r in rows
        ]


CLINICAL_RULES: list[Rule] = [
    CLN001(),
    CLN002(),
    CLN003(),
    CLN004(),
    CLN005(),
    CLN006(),
    CLN007(),
    CLN008(),
    CLN009(),
    CLN010(),
    CLN011(),
    CLN012(),
]
