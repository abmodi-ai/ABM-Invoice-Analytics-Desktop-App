"""Builds tests/fixtures/clinical/cases.json: >= 10 positive and >= 10 negative scenarios per
clinical rule (spec Phase 3). These are engineering fixtures; the billing SME replaces/extends
them with real-world scenarios (same format) before sign-off.

Case format:
  {"id", "rule", "expect": "fire"|"none", "tier": optional expected tier,
   "invoices": [{"number", "date", "freq"?, "orig"?, "lines": [{"patient", "dos", "code", "mods", "units",
                                                               "charge", "npi", "desc"?}]}]}
Patients are keys into PATIENTS; NPIs are keys into NPIS.
"""

from __future__ import annotations

import json
from pathlib import Path

PATIENTS = {
    "P1": ("Maria", "Garcia", "1980-05-17"),
    "P2": ("John", "Okafor", "1955-11-02"),
    "P3": ("Wei", "Chen", "1991-02-28"),
}
NPIS = {"N1": "1234567893", "N2": "1245319599", "N3": "1003000126"}


def L(
    patient: str,
    dos: str,
    code: str | None,
    units: float = 1,
    mods: str = "",
    charge: int = 10000,
    npi: str = "N1",
    desc: str = "",
) -> dict:
    return {
        "patient": patient,
        "dos": dos,
        "code": code,
        "units": units,
        "mods": mods,
        "charge": charge,
        "npi": npi,
        "desc": desc,
    }


def I(
    number: str, date: str, lines: list[dict], freq: str | None = None, orig: str | None = None
) -> dict:  # noqa: E743
    return {"number": number, "date": date, "lines": lines, "freq": freq, "orig": orig}


def cases() -> list[dict]:
    out: list[dict] = []

    def add(rule: str, expect: str, invoices: list[dict], tier: str | None = None, note: str = "") -> None:
        out.append(
            {
                "id": f"{rule}-{'P' if expect == 'fire' else 'N'}{sum(1 for c in out if c['rule'] == rule and c['expect'] == expect) + 1:02d}",
                "rule": rule,
                "expect": expect,
                "tier": tier,
                "note": note,
                "invoices": invoices,
            }
        )

    days = [
        f"2026-0{m}-{d:02d}"
        for m, d in [
            (2, 3),
            (2, 10),
            (3, 4),
            (3, 17),
            (4, 1),
            (4, 21),
            (5, 5),
            (5, 26),
            (6, 2),
            (6, 16),
            (7, 7),
            (7, 21),
        ]
    ]
    later = lambda d, n: d[:8] + f"{min(28, int(d[8:]) + n):02d}"  # noqa: E731

    # ---------------- CLN-001 exact duplicate line across invoices
    for i, d in enumerate(days[:10]):
        code = ["97110", "99213", "80053", "71046", "20610", "J1100", "97140", "99214", "85025", "96372"][i]
        mods = ["GP", "", "", "", "RT", "", "GP", "25", "", ""][i]
        add(
            "CLN-001",
            "fire",
            [
                I(f"A{i}", later(d, 2), [L("P1", d, code, 1, mods)]),
                I(f"B{i}", later(d, 20), [L("P1", d, code, 1, mods)]),
            ],
            "HARD",
        )
    negs = [
        ("different patient", [L("P1", days[0], "97110")], [L("P2", days[0], "97110")]),
        ("different DOS", [L("P1", days[0], "97110")], [L("P1", days[1], "97110")]),
        ("different code", [L("P1", days[0], "97110")], [L("P1", days[0], "97112")]),
        ("different units", [L("P1", days[0], "97110", 1)], [L("P1", days[0], "97110", 2)]),
        ("different modifiers", [L("P1", days[0], "20610", mods="RT")], [L("P1", days[0], "20610", mods="LT")]),
        ("different rendering NPI", [L("P1", days[0], "99213", npi="N1")], [L("P1", days[0], "99213", npi="N2")]),
        ("different patient 2", [L("P2", days[2], "80053")], [L("P3", days[2], "80053")]),
        ("different DOS 2", [L("P3", days[3], "71046")], [L("P3", days[4], "71046")]),
        ("different code 2", [L("P2", days[5], "99213")], [L("P2", days[5], "99214")]),
        ("different units 2", [L("P2", days[6], "J1100", 4)], [L("P2", days[6], "J1100", 8)]),
    ]
    for i, (note, a, b) in enumerate(negs):
        add("CLN-001", "none", [I(f"NA{i}", "2026-08-01", a), I(f"NB{i}", "2026-08-20", b)], note=note)

    # ---------------- CLN-002 same patient/DOS/code, different modifiers, no distinct/repeat/bilateral
    pos_mods = [
        ("", "GP"),
        ("25", ""),
        ("GP", "KX"),
        ("", "26"),
        ("TC", ""),
        ("GO", "GP"),
        ("", "KX"),
        ("26", "TC"),
        ("GN", ""),
        ("", "GO"),
    ]
    for i, (ma, mb) in enumerate(pos_mods):
        add(
            "CLN-002",
            "fire",
            [
                I(f"A{i}", "2026-08-02", [L("P1", days[i], "97110", 1, ma)]),
                I(f"B{i}", "2026-08-22", [L("P1", days[i], "97110", 1, mb)]),
            ],
            "PROBABLE",
        )
    neg_mods = [
        ("", "59"),
        ("", "XS"),
        ("", "XE"),
        ("", "XU"),
        ("", "XP"),
        ("", "76"),
        ("", "91"),
        ("RT", "LT"),
        ("", "50"),
        ("", "77"),
    ]
    for i, (ma, mb) in enumerate(neg_mods):
        add(
            "CLN-002",
            "none",
            [
                I(f"NA{i}", "2026-08-02", [L("P2", days[i], "71046", 1, ma)]),
                I(f"NB{i}", "2026-08-22", [L("P2", days[i], "71046", 1, mb)]),
            ],
            note=f"{mb or ma} modifier",
        )

    # ---------------- CLN-003 same patient/code +-1 day, different rendering NPI, same party
    for i, d in enumerate(days[:10]):
        add(
            "CLN-003",
            "fire",
            [
                I(f"A{i}", later(d, 3), [L("P1", d, "99213", npi="N1")]),
                I(f"B{i}", later(d, 5), [L("P1", later(d, i % 2), "99214" if False else "99213", npi="N2")]),
            ],
            "WEAK",
        )
    for i, d in enumerate(days[:10]):
        kind = i % 3
        b = (
            L("P1", later(d, 2), "99213", npi="N2")
            if kind == 0  # 2 days apart
            else (
                L("P1", later(d, 1), "99213", npi="N1")
                if kind == 1  # same NPI (CLN-001 territory)
                else L("P2", later(d, 1), "99213", npi="N2")
            )
        )  # other patient
        add(
            "CLN-003",
            "none",
            [I(f"NA{i}", later(d, 3), [L("P1", d, "99213", npi="N1")]), I(f"NB{i}", later(d, 6), [b])],
            note=["2 days apart", "same NPI", "other patient"][kind],
        )

    # ---------------- CLN-004 MUE exceeded (sample MUE: 97110=6 MAI3, 20610=2 MAI2, J1100 10->20 MAI1 by DOS)
    for i, d in enumerate(days[:5]):
        add(
            "CLN-004",
            "fire",
            [
                I(f"A{i}", later(d, 2), [L("P1", d, "97110", 4)]),
                I(f"B{i}", later(d, 12), [L("P1", d, "97110", 3, "GP")]),
            ],
            "PROBABLE",
            note="MAI 3 across claims",
        )
    for i, d in enumerate(days[5:8]):
        add(
            "CLN-004",
            "fire",
            [
                I(f"C{i}", later(d, 2), [L("P2", d, "20610", 1, "RT"), L("P2", d, "20610", 1, "LT")]),
                I(f"D{i}", later(d, 9), [L("P2", d, "20610", 1, "50")]),
            ],
            "HARD",
            note="MAI 2 absolute",
        )
    add(
        "CLN-004",
        "fire",
        [I("E1", "2025-03-10", [L("P3", "2025-03-03", "J1100", 12)])],
        "PROBABLE",
        note="MAI 1 line over the 2025-H1 MUE of 10",
    )
    add(
        "CLN-004",
        "fire",
        [I("E2", "2026-03-10", [L("P3", "2026-03-03", "J1100", 25)])],
        "PROBABLE",
        note="MAI 1 line over the current MUE of 20",
    )
    for i, d in enumerate(days[:4]):
        add(
            "CLN-004",
            "none",
            [
                I(f"NA{i}", later(d, 2), [L("P1", d, "97110", 3)]),
                I(f"NB{i}", later(d, 12), [L("P1", d, "97110", 3, "GP")]),
            ],
            note="exactly at MUE",
        )
    for i, d in enumerate(days[4:7]):
        add(
            "CLN-004",
            "none",
            [
                I(f"NC{i}", later(d, 2), [L("P2", d, "97110", 4)]),
                I(f"ND{i}", later(d, 12), [L("P2", later(d, 1), "97110", 4)]),
            ],
            note="different DOS",
        )
    add(
        "CLN-004",
        "none",
        [I("NE1", "2026-03-10", [L("P3", "2026-03-03", "J1100", 15)])],
        note="15 units: over the old MUE but DOS falls under the new MUE of 20 (effective dating)",
    )
    add(
        "CLN-004",
        "none",
        [I("NE2", "2026-03-10", [L("P3", "2026-03-03", "J1100", 8), L("P3", "2026-03-03", "J1100", 8)])],
        note="MAI 1 is per line, not per day",
    )
    add("CLN-004", "none", [I("NE3", "2026-03-10", [L("P3", "2026-03-03", "99999", 50)])], note="no MUE for code")

    # ---------------- CLN-005 NCCI PTP (sample: 20610/20604 MI0, 97140/97530 MI1, 80053/80048 MI0, 97110/97750 MI1 ended 2025)
    ptp_pos = [
        ("20610", "20604", "", ""),
        ("80053", "80048", "", ""),
        ("97140", "97530", "", ""),
        ("29881", "29880", "", ""),
        ("93000", "93005", "", ""),
        ("45380", "45378", "", ""),
        ("20610", "20604", "", "59"),
        ("80053", "80048", "", "91"),
        ("97140", "97530", "GP", "GP"),
        ("99214", "36415", "", ""),
    ]
    for i, (c1, c2, m1, m2) in enumerate(ptp_pos):
        tier = "PROBABLE"
        add(
            "CLN-005",
            "fire",
            [I(f"A{i}", "2026-08-05", [L("P1", days[i], c1, 1, m1), L("P1", days[i], c2, 1, m2)])],
            tier,
            note="MI0 ignores modifiers" if m2 else "",
        )
    ptp_neg = [
        ("97140", "97530", "", "59", "MI1 with 59 -> INFO only"),
        ("97140", "97530", "", "XS", "MI1 with XS"),
        ("99214", "36415", "25", "", "bypass on column 1"),
        ("20610", "99213", "", "", "not a PTP pair"),
        ("97110", "97750", "", "", "edit ended 2025-12-31"),
        ("80048", "85025", "", "", "not a PTP pair 2"),
        ("97140", "97530", "", "XU", "MI1 with XU"),
        ("97140", "97530", "", "XE", "MI1 with XE"),
        ("97140", "97530", "", "XP", "MI1 with XP"),
        ("11042", "97597", "", "59", "MI1 with 59"),
    ]
    for i, (c1, c2, m1, m2, note) in enumerate(ptp_neg):
        add(
            "CLN-005",
            "none",
            [I(f"N{i}", "2026-08-05", [L("P2", days[i], c1, 1, m1), L("P2", days[i], c2, 1, m2)])],
            note=note,
        )
    add(
        "CLN-005",
        "none",
        [
            I("N10", "2026-08-05", [L("P3", days[0], "20610", npi="N1")]),
            I("N11", "2026-08-06", [L("P3", days[0], "20604", npi="N2")]),
        ],
        note="different providers",
    )

    # ---------------- CLN-006 global period (sample: 29881/27447/66984 = 090, 10060/12001 = 010)
    gp = [
        ("29881", 90, 30),
        ("29881", 90, 89),
        ("27447", 90, 5),
        ("27447", 90, 60),
        ("66984", 90, 14),
        ("10060", 10, 3),
        ("10060", 10, 10),
        ("12001", 10, 7),
        ("29881", 90, 1),
        ("66984", 90, 45),
    ]
    import datetime as dt

    def plus(d: str, n: int) -> str:
        return (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()

    for i, (s_code, _g, after) in enumerate(gp):
        s = "2026-01-12"
        add(
            "CLN-006",
            "fire",
            [
                I(f"S{i}", "2026-01-15", [L("P1", s, s_code, 1, "", 150000)]),
                I(f"E{i}", plus(s, after + 3), [L("P1", plus(s, after), "99213")]),
            ],
            "PROBABLE",
        )
    gneg = [
        ("29881", 91, "", "N1", "after the 90-day window"),
        ("10060", 11, "", "N1", "after the 10-day window"),
        ("29881", 30, "24", "N1", "modifier 24"),
        ("29881", 30, "79", "N1", "modifier 79"),
        ("29881", 30, "78", "N1", "modifier 78"),
        ("29881", 30, "58", "N1", "modifier 58"),
        ("29881", 30, "", "N2", "different provider"),
        ("11042", 5, "", "N1", "000-day global"),
        ("20610", 5, "", "N1", "000-day global"),
        ("29881", 30, "57", "N1", "modifier 57"),
    ]
    for i, (s_code, after, mod, npi, note) in enumerate(gneg):
        s = "2026-01-12"
        add(
            "CLN-006",
            "none",
            [
                I(f"NS{i}", "2026-01-15", [L("P2", s, s_code, 1, "", 150000)]),
                I(f"NE{i}", plus(s, after + 3), [L("P2", plus(s, after), "99213", 1, mod, 11000, npi)]),
            ],
            note=note,
        )

    # ---------------- CLN-007 frequency limits (sample: G0439 1/YEAR, G0438 1/LIFETIME, 97110 8/WEEK, 77067 1/YEAR)
    fpos = [
        ("G0439", ["2026-02-01", "2026-09-01"]),
        ("G0439", ["2026-01-05", "2026-01-25"]),
        ("G0438", ["2024-03-01", "2026-03-01"]),
        ("77067", ["2026-01-10", "2026-12-20"]),
        ("77067", ["2026-03-01", "2026-03-15", "2026-06-01"]),
        ("G0438", ["2025-01-01", "2025-01-02"]),
        ("G0439", ["2025-02-01", "2025-11-30"]),
        ("77067", ["2025-05-05", "2025-05-06"]),
        ("G0438", ["2023-06-01", "2026-06-01"]),
        ("G0439", ["2026-04-04", "2026-04-18"]),
    ]
    for i, (code, ds) in enumerate(fpos):
        add(
            "CLN-007",
            "fire",
            [
                I(f"F{i}_{k}", plus(d, 3), [L("P1", d, code, 1, "", 14000, ["N1", "N2"][k % 2])])
                for k, d in enumerate(ds)
            ],
            "PROBABLE",
        )
    add(
        "CLN-007",
        "fire",
        [
            I("W1", "2026-03-09", [L("P3", "2026-03-02", "97110", 3), L("P3", "2026-03-04", "97110", 3)]),
            I("W2", "2026-03-10", [L("P3", "2026-03-06", "97110", 3, "GP")]),
        ],
        "PROBABLE",
        note="9 units in ISO week, limit 8",
    )
    fneg = [
        ("G0439", ["2025-11-01", "2026-02-01"], "different calendar years"),
        ("G0439", ["2026-02-01"], "single"),
        ("77067", ["2025-12-31", "2026-01-01"], "year boundary"),
        ("G0438", ["2026-01-01"], "single lifetime"),
        ("99213", ["2026-02-01", "2026-02-03", "2026-02-05"], "no limit"),
        ("77067", ["2024-01-01", "2025-01-01", "2026-01-01"], "one per year"),
        ("G0439", ["2024-06-01", "2025-06-01"], "yearly"),
        ("97110", ["2026-03-02"], "under weekly cap"),
        ("80053", ["2026-01-01", "2026-01-15"], "no limit 2"),
        ("G0439", ["2026-01-01"], "single 2"),
    ]
    for i, (code, ds, note) in enumerate(fneg):
        add("CLN-007", "none", [I(f"NF{i}_{k}", plus(d, 3), [L("P2", d, code)]) for k, d in enumerate(ds)], note=note)

    # ---------------- CLN-008 semantic duplicate (codes differ or missing, same patient + DOS)
    descs = [
        ("Therapeutic exercise each 15 minutes", "Ther exer ea 15 min"),
        ("Office visit established patient level 3", "Office visit est pt level 3"),
        ("Comprehensive metabolic panel", "Comprehensive metabolic panel test"),
        ("Chest x-ray 2 views", "Chest xray two views"),
        ("Arthrocentesis major joint", "Arthrocentesis of major joint"),
        ("Manual therapy techniques each 15 minutes", "Manual therapy techniques 15 min"),
        ("Complete blood count with automated differential", "Complete blood count w auto diff"),
        ("Therapeutic injection intramuscular", "Therapeutic inj IM"),
        ("Hemodialysis one evaluation", "Hemodialysis single evaluation"),
        ("Annual wellness visit subsequent", "Annual wellness visit, subsequent"),
    ]
    for i, (a, b) in enumerate(descs):
        add(
            "CLN-008",
            "fire",
            [
                I(f"A{i}", "2026-08-10", [L("P1", days[i], "97110" if i % 2 else None, desc=a)]),
                I(f"B{i}", "2026-08-25", [L("P1", days[i], None, desc=b)]),
            ],
            "WEAK",
        )
    dneg = [
        ("Therapeutic exercise each 15 minutes", "Chest x-ray 2 views"),
        ("Office visit established patient level 3", "Comprehensive metabolic panel"),
        ("Arthrocentesis major joint", "Routine venipuncture"),
        ("Hemodialysis one evaluation", "Screening mammography bilateral"),
        ("Manual therapy techniques", "Magnetic resonance imaging lower extremity"),
        ("Surgical trays", "Dexamethasone sodium phosphate injection"),
        ("Gait training therapy", "Complete blood count"),
        ("Incision and drainage of abscess", "Annual wellness visit"),
        ("Electrocardiogram routine", "Knee arthroscopy meniscectomy"),
        ("Neuromuscular reeducation", "Basic metabolic panel"),
    ]
    for i, (a, b) in enumerate(dneg):
        add(
            "CLN-008",
            "none",
            [
                I(f"NA{i}", "2026-08-10", [L("P2", days[i], None, desc=a)]),
                I(f"NB{i}", "2026-08-25", [L("P2", days[i], None, desc=b)]),
            ],
            note="different services",
        )

    # ---------------- CLN-009 rebill without correction
    for i, d in enumerate(days[:10]):
        add(
            "CLN-009",
            "fire",
            [
                I(f"C{i}", later(d, 2), [L("P1", d, "99213", npi="N1")], freq="1"),
                I(f"R{i}", later(d, 25), [L("P1", d, "99213", npi="N2")], freq="1"),
            ],
            "PROBABLE",
        )
    for i, d in enumerate(days[:10]):
        kind = i % 4
        if kind == 0:
            b = I(f"NR{i}", later(d, 25), [L("P2", d, "99213", npi="N2")], freq="7", orig=f"NC{i}")
            note = "replacement claim (7)"
        elif kind == 1:
            b = I(f"NR{i}", later(d, 25), [L("P2", d, "99213", npi="N2")], freq="8", orig=f"NC{i}")
            note = "void claim (8)"
        elif kind == 2:
            b = I(f"NR{i}", later(d, 25), [L("P2", later(d, 1), "99213", npi="N2")], freq="1")
            note = "different DOS"
        else:
            b = I(f"NR{i}", later(d, 25), [L("P2", d, "99214", npi="N2")], freq="1")
            note = "different code"
        add("CLN-009", "none", [I(f"NC{i}", later(d, 2), [L("P2", d, "99213", npi="N1")], freq="1"), b], note=note)
    return out


if __name__ == "__main__":
    cs = cases()
    Path(__file__).with_name("cases.json").write_text(
        json.dumps({"patients": PATIENTS, "npis": NPIS, "cases": cs}, indent=1)
    )
    from collections import Counter

    print(Counter((c["rule"], c["expect"]) for c in cs))
