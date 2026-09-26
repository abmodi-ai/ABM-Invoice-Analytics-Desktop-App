"""Party and patient name normalization, phonetic keys, and a nickname table."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from metaphone import doublemetaphone  # BSD-licensed `metaphone` package

LEGAL_SUFFIXES = {
    "INC",
    "LLC",
    "LLP",
    "PC",
    "PA",
    "CORP",
    "CO",
    "LTD",
    "PLLC",
    "LP",
    "CORPORATION",
    "INCORPORATED",
    "COMPANY",
    "LIMITED",
}
PARTY_ABBREVIATIONS = {
    "MED": "MEDICAL",
    "CTR": "CENTER",
    "CNTR": "CENTER",
    "ASSOC": "ASSOCIATES",
    "ASSN": "ASSOCIATION",
    "HOSP": "HOSPITAL",
    "SVCS": "SERVICES",
    "SVC": "SERVICE",
    "GRP": "GROUP",
    "MGMT": "MANAGEMENT",
    "INTL": "INTERNATIONAL",
    "NATL": "NATIONAL",
    "UNIV": "UNIVERSITY",
    "HLTH": "HEALTH",
    "PHYS": "PHYSICIANS",
    "ORTHO": "ORTHOPEDIC",
    "REHAB": "REHABILITATION",
    "LAB": "LABORATORY",
    "LABS": "LABORATORIES",
    "DIAG": "DIAGNOSTIC",
    "SYS": "SYSTEMS",
    "TECH": "TECHNOLOGY",
    "&": "AND",
    "ST": "SAINT",
    "MT": "MOUNT",
    "DEPT": "DEPARTMENT",
    "SUPPL": "SUPPLY",
}
NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "MD", "DO", "PHD", "RN", "NP", "PA", "DDS"}

# Nickname table used by patient first-name comparison (subset; extend in refdata if needed).
NICKNAMES: dict[str, set[str]] = {
    "WILLIAM": {"BILL", "BILLY", "WILL", "WILLIE", "LIAM"},
    "ROBERT": {"BOB", "BOBBY", "ROB", "ROBBIE", "BERT"},
    "RICHARD": {"RICK", "RICKY", "DICK", "RICH"},
    "JAMES": {"JIM", "JIMMY", "JAMIE"},
    "JOHN": {"JACK", "JOHNNY", "JON"},
    "JOSEPH": {"JOE", "JOEY"},
    "MICHAEL": {"MIKE", "MIKEY", "MICK"},
    "THOMAS": {"TOM", "TOMMY"},
    "CHARLES": {"CHUCK", "CHARLIE", "CHAS"},
    "CHRISTOPHER": {"CHRIS", "KIT"},
    "DANIEL": {"DAN", "DANNY"},
    "MATTHEW": {"MATT"},
    "ANTHONY": {"TONY"},
    "EDWARD": {"ED", "EDDIE", "TED", "NED"},
    "STEVEN": {"STEVE"},
    "STEPHEN": {"STEVE"},
    "ANDREW": {"ANDY", "DREW"},
    "BENJAMIN": {"BEN", "BENNY"},
    "SAMUEL": {"SAM", "SAMMY"},
    "ALEXANDER": {"ALEX", "AL", "SASHA"},
    "NICHOLAS": {"NICK", "NICKY"},
    "PATRICK": {"PAT", "PADDY"},
    "TIMOTHY": {"TIM", "TIMMY"},
    "GREGORY": {"GREG"},
    "KENNETH": {"KEN", "KENNY"},
    "RONALD": {"RON", "RONNIE"},
    "DONALD": {"DON", "DONNIE"},
    "LAWRENCE": {"LARRY"},
    "ELIZABETH": {"LIZ", "BETH", "BETTY", "LIZZIE", "ELIZA", "BETSY"},
    "MARGARET": {"MAGGIE", "PEGGY", "MEG", "MARGE"},
    "KATHERINE": {"KATE", "KATHY", "KATIE", "KAT"},
    "CATHERINE": {"CATHY", "KATE", "CAT"},
    "JENNIFER": {"JEN", "JENNY"},
    "PATRICIA": {"PAT", "PATTY", "TRISH"},
    "SUSAN": {"SUE", "SUSIE"},
    "DEBORAH": {"DEB", "DEBBIE"},
    "REBECCA": {"BECKY", "BECCA"},
    "VICTORIA": {"VICKY", "TORI"},
    "ABIGAIL": {"ABBY"},
    "BARBARA": {"BARB", "BARBIE"},
    "DOROTHY": {"DOT", "DOTTIE"},
    "CHRISTINE": {"CHRIS", "CHRISSY", "TINA"},
    "JESSICA": {"JESS", "JESSIE"},
    "SAMANTHA": {"SAM", "SAMMY"},
    "ALEXANDRA": {"ALEX", "SASHA", "LEXI"},
    "THERESA": {"TERRY", "TESS"},
    "JOSEPHINE": {"JO", "JOSIE"},
}
_NICK_CANON: dict[str, str] = {}
for _full, _nicks in NICKNAMES.items():
    _NICK_CANON.setdefault(_full, _full)
    for _n in _nicks:
        _NICK_CANON.setdefault(_n, _full)


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_party_name(raw: str | None) -> str:
    if not raw:
        return ""
    s = strip_diacritics(raw).casefold().upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    toks = [PARTY_ABBREVIATIONS.get(t, t) for t in s.split()]
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    if toks and toks[0] == "THE":
        toks = toks[1:]
    return " ".join(toks)


@dataclass(frozen=True)
class PersonName:
    first: str
    last: str
    last_tokens: tuple[str, ...]
    first_phonetic: str
    last_phonetic: str


def _clean_person(s: str | None) -> str:
    s = strip_diacritics(s or "").casefold().upper()
    s = re.sub(r"[^A-Z \-']", " ", s)
    s = s.replace("'", "")
    return " ".join(s.split())


def phonetic(s: str) -> str:
    if not s:
        return ""
    primary, _secondary = doublemetaphone(s.replace(" ", "").replace("-", ""))
    return str(primary)


def normalize_person_name(first: str | None, last: str | None) -> PersonName:
    f_toks = [t for t in _clean_person(first).replace("-", " ").split() if t not in NAME_SUFFIXES]
    l_clean = _clean_person(last)
    l_toks = [t for t in l_clean.replace("-", " ").split() if t not in NAME_SUFFIXES]
    f = f_toks[0] if f_toks else ""
    lst = " ".join(l_toks)
    return PersonName(
        first=f,
        last=lst,
        last_tokens=tuple(l_toks),
        first_phonetic=phonetic(f),
        last_phonetic=phonetic(lst),
    )


def canonical_first_name(first: str) -> str:
    return _NICK_CANON.get(first.upper(), first.upper())


def split_full_name(full: str) -> tuple[str, str]:
    """'LAST, FIRST M' or 'FIRST M LAST' -> (first, last)."""
    s = full.strip()
    if "," in s:
        last, rest = s.split(",", 1)
        return (rest.strip().split(" ")[0] if rest.strip() else ""), last.strip()
    parts = s.split()
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]
