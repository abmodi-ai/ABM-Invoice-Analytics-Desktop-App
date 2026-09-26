"""Rule catalog (spec section 5.4)."""

from invoice_analytics.rules.base import TIER_RANK, TIERS, Candidate, Rule, RuleContext
from invoice_analytics.rules.clinical_rules import CLINICAL_RULES
from invoice_analytics.rules.invoice_rules import INVOICE_RULES

ALL_RULES: list[Rule] = [*INVOICE_RULES, *CLINICAL_RULES]
RULES_BY_ID: dict[str, Rule] = {r.rule_id: r for r in ALL_RULES}

__all__ = ["ALL_RULES", "RULES_BY_ID", "TIERS", "TIER_RANK", "Candidate", "Rule", "RuleContext"]
