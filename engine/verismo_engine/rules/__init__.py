"""Rule catalog (spec section 5.4)."""

from verismo_engine.rules.base import TIER_RANK, TIERS, Candidate, Rule, RuleContext
from verismo_engine.rules.clinical_rules import CLINICAL_RULES
from verismo_engine.rules.invoice_rules import INVOICE_RULES

ALL_RULES: list[Rule] = [*INVOICE_RULES, *CLINICAL_RULES]
RULES_BY_ID: dict[str, Rule] = {r.rule_id: r for r in ALL_RULES}

__all__ = ["ALL_RULES", "RULES_BY_ID", "TIERS", "TIER_RANK", "Candidate", "Rule", "RuleContext"]
