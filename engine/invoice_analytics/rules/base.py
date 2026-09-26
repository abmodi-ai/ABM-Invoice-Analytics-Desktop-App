"""Rule framework. Every rule is a pure function of (records, reference data, config).

Rules read the DuckDB mirror (tables `inv`, `lines`, `scope`, ref_*) and return Candidates.
They never write. Orientation convention: the subject is the later record (by invoice date,
then id); counterparts are the earlier records it duplicates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

import duckdb

if TYPE_CHECKING:
    from invoice_analytics.scoring.store import DetectionStore

TIERS = ("INFO", "WEAK", "PROBABLE", "HARD")
_INV_REF = re.compile(r"\b(FROM|JOIN)\s+inv(\s)")
_LINES_REF = re.compile(r"\b(FROM|JOIN)\s+lines(\s)")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}


def escalate(tier: str, steps: int = 1) -> str:
    return TIERS[min(len(TIERS) - 1, TIER_RANK[tier] + steps)]


def downgrade(tier: str, steps: int = 1) -> str:
    return TIERS[max(0, TIER_RANK[tier] - steps)]


def max_tier(tiers: list[str]) -> str:
    return max(tiers, key=lambda t: TIER_RANK[t]) if tiers else "INFO"


@dataclass
class Candidate:
    rule_id: str
    subject_type: str  # INVOICE | LINE
    subject_id: int
    counterpart_ids: tuple[int, ...]
    tier: str
    score: float
    summary_params: dict[str, Any] = field(default_factory=dict)
    methods: dict[str, Any] = field(default_factory=dict)  # field -> method / {method, value}
    refdata: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    # filled by the pipeline
    base_tier: str = ""
    suppressed_by: str | None = None
    downgraded_by: list[str] = field(default_factory=list)
    suppressions_considered: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str, int, tuple[int, ...]]:
        return (self.rule_id, self.subject_type, self.subject_id, tuple(sorted(self.counterpart_ids)))


@dataclass
class RuleContext:
    store: DetectionStore
    con: duckdb.DuckDBPyConnection
    config: dict[str, Any]
    incremental: bool
    all_settings: dict[str, Any] = field(default_factory=dict)

    def q(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        if self.incremental:
            # Incremental runs join against candidate tables pre-restricted to the party clusters,
            # shared identifiers and patient clusters touched by the new invoices (see
            # DetectionStore.scope_table). Rules keep writing plain `inv` / `lines`.
            sql = _INV_REF.sub(r"\1 inv_c\2", sql)
            sql = _LINES_REF.sub(r"\1 lines_c\2", sql)
        cur = self.con.execute(sql, params or [])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def qnp(self, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        """Column-wise numpy result for large candidate sets (avoids millions of dicts)."""
        if self.incremental:
            sql = _INV_REF.sub(r"\1 inv_c\2", sql)
            sql = _LINES_REF.sub(r"\1 lines_c\2", sql)
        return dict(self.con.execute(sql, params or []).fetchnumpy())


class Rule:
    rule_id: ClassVar[str]
    version: ClassVar[str] = "1"
    subject_type: ClassVar[str]
    default_tier: ClassVar[str]
    base_score: ClassVar[float] = 0.5
    title: ClassVar[str]
    summary_template: ClassVar[str]
    uses_refdata: ClassVar[tuple[str, ...]] = ()

    def run(self, ctx: RuleContext) -> list[Candidate]:
        raise NotImplementedError

    def tier(self, ctx: RuleContext) -> str:
        t = ctx.config.get("tier", self.default_tier)
        return t if t in TIER_RANK else self.default_tier

    def describe(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "title": self.title,
            "subject_type": self.subject_type,
            "default_tier": self.default_tier,
            "summary_template": self.summary_template,
            "uses_refdata": list(self.uses_refdata),
        }


# SQL fragments shared by rules -------------------------------------------------------------
# Pair orientation: `a` is the earlier record, `b` the later (subject).
INV_ORDER = "a.sort_key < b.sort_key"
LINE_ORDER = "(a.inv_sort < b.inv_sort OR (a.inv_sort = b.inv_sort AND a.id < b.id))"
INV_SCOPE = "(a.id IN (SELECT id FROM scope) OR b.id IN (SELECT id FROM scope))"
LINE_SCOPE = "(a.invoice_id IN (SELECT id FROM scope) OR b.invoice_id IN (SELECT id FROM scope))"
LIVE_INV = "{t}.status <> 'VOID'"

# Invoice-level amount/number rules skip pairs whose invoices bill disjoint sets of patients:
# two invoices for different patients are not the same bill, whatever their totals.
PATS_CTE = (
    'pats AS (SELECT invoice_id, list(DISTINCT patient_cluster) AS p FROM "lines"'
    " WHERE patient_cluster IS NOT NULL AND invoice_id IN (SELECT id FROM inv ) GROUP BY invoice_id)"
)
PATS_JOIN = "LEFT JOIN pats pa ON pa.invoice_id = a.id LEFT JOIN pats pb ON pb.invoice_id = b.id"
PATS_OK = "(pa.p IS NULL OR pb.p IS NULL OR list_has_any(pa.p, pb.p))"
