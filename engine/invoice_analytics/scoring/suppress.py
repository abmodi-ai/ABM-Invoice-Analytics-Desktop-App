"""Suppression rules SUP-001 .. SUP-006, applied after detection (spec 5.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from invoice_analytics.rules.base import Candidate, downgrade

REPEAT_MODS = {"76", "77", "91"}
BILATERAL_PAIRS = ({"RT"}, {"LT"})
SUP006_RULES = {"CLN-003", "CLN-008", "CLN-009", "CLN-001"}
# A recurring series explains repeated services; it never excuses exceeding an explicit cap
# (CLN-004 MUE, CLN-007 frequency limit), so those rules are not downgraded.
SUP004_RULES = {"CLN-003", "CLN-009", "INV-005", "CLN-001", "CLN-011"}


def pair_key(subject_type: str, ids: list[int] | tuple[int, ...]) -> str:
    return f"{subject_type}:" + ",".join(str(i) for i in sorted(set(ids)))


def expected_interval(freq: str) -> tuple[float, float] | None:
    """(min_days, max_days) between two services in a recurring series."""
    f = freq.upper().strip()
    if f == "DAILY":
        return (1, 1)
    if f == "WEEKLY":
        return (7, 7)
    if f == "BIWEEKLY":
        return (14, 14)
    if f == "MONTHLY":
        return (28, 31)
    if m := re.match(r"^(\d)X_WEEK$", f):
        n = int(m.group(1))
        return (1, 7 / n + 1)
    if m := re.match(r"^EVERY_(\d+)D$", f):
        d = int(m.group(1))
        return (d, d)
    return None


@dataclass
class SuppressionData:
    """Everything the suppression pass needs, prefetched in bulk."""

    invoices: dict[int, dict[str, Any]]
    lines: dict[int, dict[str, Any]]
    links: set[tuple[int, int, str]] = field(default_factory=set)  # (from, to, type)
    not_dup_pairs: set[str] = field(default_factory=set)
    netted_invoices: set[int] = field(default_factory=set)  # originals netted out by a credit
    recurring: dict[str, list[str]] = field(default_factory=dict)  # code -> typical freqs


def _inv_of(c: Candidate, data: SuppressionData) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if c.subject_type == "INVOICE":
        s = data.invoices.get(c.subject_id)
        cps = [data.invoices[i] for i in c.counterpart_ids if i in data.invoices]
    else:
        sl = data.lines.get(c.subject_id)
        s = data.invoices.get(sl["invoice_id"]) if sl else None
        cps = [
            data.invoices[data.lines[i]["invoice_id"]]
            for i in c.counterpart_ids
            if i in data.lines and data.lines[i]["invoice_id"] in data.invoices
        ]
    return s, cps


def _mods(line: dict[str, Any]) -> set[str]:
    m = line.get("mods") or ""
    return set(m.split(",")) if m else set()


def apply_suppressions(cands: list[Candidate], data: SuppressionData, cfg: dict[str, Any]) -> None:
    def on(sup: str) -> bool:
        return bool(cfg.get(f"suppress.{sup}", {}).get("enabled", True))

    tol = int(cfg.get("suppress.SUP-001", {}).get("net_tolerance_cents", 0))
    slack = float(cfg.get("suppress.SUP-004", {}).get("interval_slack_days", 1))
    for c in cands:
        c.base_tier = c.base_tier or c.tier
        s_inv, cp_invs = _inv_of(c, data)
        considered = c.suppressions_considered
        if s_inv is None:
            continue
        pair_invs = [s_inv, *cp_invs]
        # SUP-005 prior reviewer decision on the same pair
        if on("SUP-005"):
            if pair_key(c.subject_type, (c.subject_id, *c.counterpart_ids)) in data.not_dup_pairs:
                c.suppressed_by = "SUP-005"
                considered.append("SUP-005: a reviewer previously marked this pair NOT_DUPLICATE")
                continue
            considered.append("SUP-005: no prior NOT_DUPLICATE decision for this pair")
        # SUP-001 credits netting out the original
        if on("SUP-001"):
            hit = False
            for cp in cp_invs:
                credit_side = s_inv["is_credit"] or cp["is_credit"] or s_inv["total"] < 0 or cp["total"] < 0
                if credit_side and abs(s_inv["total"] + cp["total"]) <= tol:
                    hit = True
                if cp["id"] in data.netted_invoices:
                    hit = True
            if c.subject_type == "LINE":
                subj_line = data.lines[c.subject_id]
                for i in c.counterpart_ids:
                    cl = data.lines.get(i)
                    if (
                        cl
                        and (subj_line["charge"] < 0 or cl["charge"] < 0)
                        and abs(subj_line["charge"] + cl["charge"]) <= tol
                    ):
                        hit = True
            elif s_inv["is_credit"] or s_inv["total"] < 0:
                hit = hit or any(abs(s_inv["total"] + cp["total"]) <= tol for cp in cp_invs)
            if hit:
                c.suppressed_by = "SUP-001"
                considered.append("SUP-001: a credit or negative invoice nets out the original")
                continue
            considered.append("SUP-001: no netting credit found")
        # SUP-002 / SUP-003 replacement and void claims
        ids = [i["id"] for i in pair_invs]
        rel = {(a, b, t) for (a, b, t) in data.links if a in ids and b in ids}
        freq_codes = {i["freq"] for i in pair_invs}
        if on("SUP-002"):
            if any(t == "REPLACES" for _, _, t in rel):
                c.suppressed_by = "SUP-002"
                considered.append("SUP-002: frequency code 7 replacement references the original")
                continue
            considered.append(
                "SUP-002: no frequency code 7 present"
                if "7" not in freq_codes
                else "SUP-002: frequency code 7 present but does not reference the counterpart"
            )
        if on("SUP-003"):
            if any(t == "VOIDS" for _, _, t in rel):
                c.suppressed_by = "SUP-003"
                considered.append("SUP-003: frequency code 8 void references the original")
                continue
            considered.append(
                "SUP-003: no frequency code 8 void present"
                if "8" not in freq_codes
                else "SUP-003: frequency code 8 present but does not reference the counterpart"
            )
        if c.subject_type != "LINE":
            continue
        sl = data.lines.get(c.subject_id)
        if sl is None:
            continue
        cls = [data.lines[i] for i in c.counterpart_ids if i in data.lines]
        # SUP-006 repeat / bilateral modifiers correctly applied
        if on("SUP-006") and c.rule_id in SUP006_RULES and cls:
            sm = _mods(sl)
            ok = False
            for cl in cls:
                cm = _mods(cl)
                if sm & REPEAT_MODS and not (cm & REPEAT_MODS & sm):
                    ok = True
                if ("RT" in sm and "LT" in cm) or ("LT" in sm and "RT" in cm):
                    ok = True
                if "50" in sm and "50" not in cm:
                    ok = True
            if ok:
                c.downgraded_by.append("SUP-006")
                c.tier = "INFO"
                considered.append("SUP-006: repeat or bilateral modifier correctly applied; downgraded to INFO")
            else:
                considered.append("SUP-006: no repeat or bilateral modifier distinguishes the lines")
        # SUP-004 recurring series with the expected interval
        if on("SUP-004") and c.rule_id in SUP004_RULES and cls and sl.get("code") in data.recurring:
            gaps = []
            for cl in cls:
                if sl.get("dos") and cl.get("dos"):
                    gaps.append(abs((sl["dos"] - cl["dos"]).days))
            nearest = min(gaps) if gaps else None
            matched = False
            if nearest is not None and nearest > 0:
                for f in data.recurring[sl["code"]]:
                    rng = expected_interval(f)
                    if rng and rng[0] - slack <= nearest <= rng[1] + slack:
                        matched = True
            if matched:
                c.downgraded_by.append("SUP-004")
                c.tier = downgrade(c.tier)
                considered.append(f"SUP-004: {sl['code']} is a recurring series and the {nearest}-day interval matches")
            else:
                considered.append("SUP-004: interval does not match the code's recurring series")
