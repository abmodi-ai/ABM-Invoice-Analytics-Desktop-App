"""Procedure codes, modifiers and free-text descriptions."""

from __future__ import annotations

import re
from collections.abc import Iterable

CPT_RE = re.compile(r"^\d{4}[0-9A-Z]$")
HCPCS_RE = re.compile(r"^[A-V]\d{4}$")
REV_RE = re.compile(r"^0?\d{3}$")
MODIFIER_RE = re.compile(r"^[0-9A-Z]{2}$")


def normalize_code(raw: str | None) -> str | None:
    if raw is None:
        return None
    compact = re.sub(r"\s+", "", str(raw)).upper()
    if len(compact) <= 7:  # CPT / HCPCS / revenue codes: "992 13" -> "99213"
        return compact or None
    # service labels on trial invoices ("CT CHEST W CONTRAST"): keep words, collapse spacing
    return " ".join(str(raw).upper().split())[:80] or None


def classify_code(code: str | None) -> str:
    if not code:
        return "OTHER"
    if CPT_RE.match(code):
        return "CPT"
    if HCPCS_RE.match(code):
        return "HCPCS"
    if REV_RE.match(code):
        return "REV"
    return "OTHER"


def normalize_modifiers(mods: Iterable[str | None] | str | None) -> list[str]:
    """Modifiers become a sorted set of valid 2-char codes."""
    if mods is None:
        return []
    if isinstance(mods, str):
        items: Iterable[str | None] = re.split(r"[\s,;:|/]+", mods)
    else:
        items = mods
    out = set()
    for m in items:
        if not m:
            continue
        s = str(m).strip().upper()
        if MODIFIER_RE.match(s):
            out.add(s)
    return sorted(out)


def is_em_code(code: str | None) -> bool:
    """Evaluation and management CPT range 99202-99499."""
    return bool(code and code.isdigit() and 99202 <= int(code) <= 99499)


# Curated abbreviation table for descriptions. Keys are lowercase tokens.
DESC_ABBREVIATIONS = {
    "pt": "physical therapy",
    "ot": "occupational therapy",
    "inj": "injection",
    "eval": "evaluation",
    "evals": "evaluations",
    "reeval": "re evaluation",
    "ther": "therapeutic",
    "exer": "exercise",
    "exerc": "exercise",
    "proc": "procedure",
    "consult": "consultation",
    "tx": "treatment",
    "dx": "diagnosis",
    "hx": "history",
    "fu": "follow up",
    "f/u": "follow up",
    "ov": "office visit",
    "est": "established",
    "estab": "established",
    "pt.": "patient",
    "lvl": "level",
    "lev": "level",
    "min": "minutes",
    "mins": "minutes",
    "hr": "hour",
    "hrs": "hours",
    "xray": "x ray",
    "xr": "x ray",
    "mri": "magnetic resonance imaging",
    "ct": "computed tomography",
    "us": "ultrasound",
    "ekg": "electrocardiogram",
    "ecg": "electrocardiogram",
    "lab": "laboratory",
    "cbc": "complete blood count",
    "iv": "intravenous",
    "im": "intramuscular",
    "sq": "subcutaneous",
    "subq": "subcutaneous",
    "w/": "with",
    "w/o": "without",
    "wo": "without",
    "bilat": "bilateral",
    "rt": "right",
    "lt": "left",
    "ea": "each",
    "addl": "additional",
    "add'l": "additional",
    "manip": "manipulation",
    "neuromusc": "neuromuscular",
    "reed": "reeducation",
    "re-ed": "reeducation",
    "w": "with",
    "auto": "automated",
    "diff": "differential",
    "diffs": "differential",
    "single": "1",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "mod": "moderate",
    "comp": "comprehensive",
    "cplx": "complex",
    "hosp": "hospital",
    "adm": "admission",
    "anes": "anesthesia",
    "vacc": "vaccine",
    "admin": "administration",
}
STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "for",
    "and",
    "or",
    "to",
    "in",
    "on",
    "by",
    "per",
    "at",
    "with",
    "is",
    "each",
    "each15",
    "unit",
    "units",
    "service",
    "services",
}


def normalize_description(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.lower()
    s = re.sub(r"(\d+)\s*(min|mins|minutes)\b", r"\1 minutes", s)
    toks: list[str] = []
    prev = ""
    for t in re.split(r"[\s,;:()\[\]]+", s):
        t = t.strip(".-")
        if not t:
            continue
        # "pt" is "patient" after new/est/established, otherwise "physical therapy"
        if t == "pt" and prev in ("new", "est", "estab", "established"):
            expanded = "patient"
        else:
            expanded = DESC_ABBREVIATIONS.get(t, t)
        prev = t
        for e in re.split(r"[\s/\-]+", expanded):
            if e and e not in STOPWORDS:
                toks.append(e)
    return " ".join(toks)
