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
    visit: str | None = None,
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
        "visit": visit,
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
    # ---------------- CLN-010 same charge twice on one invoice
    c10 = [
        ("99213", 11000, None, None),
        ("97110", 4500, None, None),
        ("TRAVEL REIMBURSEMENT", 8250, "D-8", "D-7"),
        ("80053", 4000, None, None),
        ("CT CHEST W CONTRAST", 412000, "M2", "M2"),
        ("J1100", 500, None, None),
        ("SCREENING", 650000, "Screening", "Screening"),
        ("71046", 8000, None, None),
        ("36415", 1000, None, None),
        ("LEUKAPHERESIS", 1442308, "Leukapheresis", "Leukapheresis"),
    ]
    for i, (code, ch, va, vb) in enumerate(c10):
        d = days[i]
        add(
            "CLN-010",
            "fire",
            [I(f"A{i}", later(d, 3), [L("P1", d, code, charge=ch, visit=va), L("P1", d, code, charge=ch, visit=vb)])],
            "PROBABLE",
        )
    n10 = [
        ("different amount", [L("P2", days[0], "99213", charge=11000), L("P2", days[0], "99213", charge=11500)]),
        ("different date", [L("P2", days[1], "97110"), L("P2", later(days[1], 1), "97110")]),
        ("different patient", [L("P2", days[2], "80053"), L("P3", days[2], "80053")]),
        (
            "different item",
            [
                L("P2", days[3], "CT CHEST W CONTRAST", charge=412000),
                L("P2", days[3], "CT PELVIS W IV CONTRAST", charge=412000),
            ],
        ),
        (
            "repeat modifier 91",
            [L("P2", days[4], "80048", charge=3000), L("P2", days[4], "80048", mods="91", charge=3000)],
        ),
        ("bilateral RT/LT", [L("P2", days[5], "20610", mods="RT"), L("P2", days[5], "20610", mods="LT")]),
        ("distinct modifier 59", [L("P2", days[6], "97140"), L("P2", days[6], "97140", mods="59")]),
        ("different units", [L("P2", days[7], "J1100", 4, charge=2000), L("P2", days[7], "J1100", 8, charge=2000)]),
        ("zero charge", [L("P3", days[8], "99211", charge=0), L("P3", days[8], "99211", charge=0)]),
        ("single line", [L("P3", days[9], "99214")]),
    ]
    for i, (note, lines) in enumerate(n10):
        add("CLN-010", "none", [I(f"NA{i}", "2026-08-01", lines)], note=note)

    # ---------------- CLN-011 high-cost service repeated for the same patient (>= $1,000, 1-30 days)
    for i, (code, ch, gap) in enumerate(
        [
            ("CT CHEST W CONTRAST", 412000, 21),
            ("73721", 150000, 7),
            ("CT ABDOMEN W IV CONTRAST", 433500, 1),
            ("BIOPSY: LUNG OR MEDIASTINUM", 1537250, 30),
            ("29881", 180000, 14),
            ("70553", 250000, 10),
            ("STEM CELL/MARROW FREEZING", 631410, 3),
            ("CT PELVIS W IV CONTRAST", 389900, 28),
            ("PET/CT", 420000, 20),
            ("LIVER BIOPSY", 512300, 5),
        ]
    ):
        d0 = "2026-03-02"
        add(
            "CLN-011",
            "fire",
            [
                I(f"A{i}", "2026-03-05", [L("P1", d0, code, charge=ch, visit="Screening")]),
                I(f"B{i}", "2026-04-15", [L("P1", plus(d0, gap), code, charge=ch, visit="Screening")]),
            ],
            "PROBABLE",
        )
    n11 = [
        ("below $1,000", "99214", 16500, 7, "P2"),
        ("outside 30 days", "CT CHEST W CONTRAST", 412000, 31, "P2"),
        ("different patient", "CT CHEST W CONTRAST", 412000, 10, "P3"),
        ("different amount", "73721", 150000, 7, "P2"),
        ("same day (CLN-001/010 territory)", "70553", 250000, 0, "P2"),
        ("below $1,000 2", "97110", 9000, 3, "P2"),
        ("outside window 2", "29881", 180000, 60, "P2"),
        ("different item", "CT CHEST W CONTRAST", 412000, 10, "P2"),
        ("below $1,000 3", "TRAVEL REIMBURSEMENT", 8250, 2, "P2"),
        ("different amount 2", "LIVER BIOPSY", 512300, 5, "P2"),
    ]
    for i, (note, code, ch, gap, other_patient) in enumerate(n11):
        d0 = "2026-05-04"
        second_code = "CT PELVIS W IV CONTRAST" if note == "different item" else code
        second_ch = ch + 100 if note.startswith("different amount") else ch
        add(
            "CLN-011",
            "none",
            [
                I(f"NA{i}", "2026-05-06", [L("P2", d0, code, charge=ch)]),
                I(f"NB{i}", "2026-07-20", [L(other_patient, plus(d0, gap), second_code, charge=second_ch)]),
            ],
            note=note,
        )

    # ---------------- CLN-012 two protocol timepoints on the same date
    pairs12 = [
        ("D-8", "D-7"),
        ("D-1", "D0"),
        ("D5", "D7"),
        ("M1", "M2"),
        ("D14", "D21"),
        ("D0", "D1"),
        ("D-6", "D-5"),
        ("M1D1", "M1D2"),
        ("D28", "M2"),
        ("D-4", "D-3"),
    ]
    for i, (va, vb) in enumerate(pairs12):
        d = days[i]
        add(
            "CLN-012",
            "fire",
            [
                I(f"A{i}", later(d, 5), [L("P1", d, f"COHORT 1: {va}", charge=100000 + i, visit=va)]),
                I(f"B{i}", later(d, 9), [L("P1", d, f"COHORT 1: {vb}", charge=200000 + i, visit=vb)]),
            ],
            "PROBABLE",
        )
    n12 = [
        ("same timepoint, several items", [("D-8", "CT GUIDED NEEDLE PLACEMENT"), ("D-8", "BIOPSY")], "P2", False),
        ("different dates", [("D-1", "COHORT 1: D-1"), ("D0", "COHORT 1: D0")], "P2", True),
        (
            "not timepoints (Screening + Leukapheresis)",
            [("Screening", "SCREENING"), ("Leukapheresis", "LEUKAPHERESIS")],
            "P2",
            False,
        ),
        ("no visit labels (claims)", [(None, "99213"), (None, "80053")], "P2", False),
        ("different subjects", [("D-1", "COHORT 1: D-1"), ("D0", "COHORT 1: D0")], "split", False),
        ("one visit only", [("D5", "D5")], "P2", False),
        ("same timepoint on two invoices", [("M2", "CT CHEST"), ("M2", "CT PELVIS")], "P2", False),
        (
            "screening + timepoint on different dates",
            [("Screening", "SCREENING"), ("D-8", "COHORT 1: D-8")],
            "P2",
            True,
        ),
        ("baseline label only", [("Baseline", "BASELINE"), ("Baseline", "BASELINE LABS")], "P2", False),
        ("timepoint + non-timepoint same day", [("Screening", "SCREENING"), ("D-8", "COHORT 1: D-8")], "P2", False),
    ]
    for i, (note, items, who, shift) in enumerate(n12):
        d = days[i]
        invs = []
        for k, (visit, code) in enumerate(items):
            patient = ("P2" if k == 0 else "P3") if who == "split" else who
            dos = later(d, k * 2) if shift else d
            invs.append(I(f"N12{i}_{k}", later(d, 9), [L(patient, dos, code, charge=300000 + k, visit=visit)]))
        add("CLN-012", "none", invs, note=note)

    # ---------------- CLN-013 protocol visit already billed on another invoice (date differs)
    visits13 = [
        ("M1D1", "MONTH 1 DAY 1", 183900, 3),
        ("D-8", "COHORT 1: D-8", 385000, 1),
        ("D0", "COHORT 2: D0", 1675000, 14),
        ("Screening", "SCREENING", 650000, 30),
        ("D-8", "TRAVEL REIMBURSEMENT", 8250, 2),
        ("M2", "CT CHEST W CONTRAST", 412000, 7),
        ("Leukapheresis", "COHORT 1: LEUKAPHERESIS", 990000, 5),
        ("D28", "D28", 120000, 60),
        ("M3D1", "MONTH 3 DAY 1", 365200, 1),
        ("D-5", "LYMPHODEPLETION DAY -5", 117600, 10),
    ]
    for i, (visit, code, ch, gap) in enumerate(visits13):
        d = days[i]
        amount_b = ch if i % 2 == 0 else ch + 5000  # the repeat is caught even at another amount
        add(
            "CLN-013",
            "fire",
            [
                I(f"A13{i}", later(d, 5), [L("P1", d, code, charge=ch, visit=visit)]),
                I(f"B13{i}", later(d, 40 + gap), [L("P1", later(d, gap), code, charge=amount_b, visit=visit)]),
            ],
            "PROBABLE",
        )
    n13 = [
        ("same invoice (CLN-010/012 territory)", "same_invoice"),
        ("different subject", "other_patient"),
        ("different visit", "other_visit"),
        ("different item at same visit", "other_item"),
        ("same date (CLN-001 territory)", "same_date"),
        ("no visit label (claims)", "no_visit"),
        ("zero charge", "zero"),
        ("single invoice, single line", "single"),
        ("different visit 2", "other_visit"),
        ("different subject 2", "other_patient"),
    ]
    for i, (note, kind) in enumerate(n13):
        d = days[i]
        visit, code, ch = "M1D1", "MONTH 1 DAY 1", 183900
        first = L("P2", d, code, charge=ch, visit=None if kind == "no_visit" else visit)
        second = L(
            "P3" if kind == "other_patient" else "P2",
            d if kind == "same_date" else later(d, 7),
            "MONTH 1 DAY 2" if kind == "other_item" else ("99213" if kind == "no_visit" else code),
            charge=0 if kind == "zero" else ch,
            visit=None if kind == "no_visit" else ("M1D2" if kind == "other_visit" else visit),
        )
        if kind == "no_visit":
            first = L("P2", d, "99213", charge=ch)
        if kind == "zero":
            first = L("P2", d, code, charge=0, visit=visit)
        if kind == "same_invoice":
            invs = [I(f"N13{i}", later(d, 9), [first, second])]
        elif kind == "single":
            invs = [I(f"N13{i}", later(d, 9), [first])]
        else:
            invs = [I(f"N13{i}a", later(d, 5), [first]), I(f"N13{i}b", later(d, 30), [second])]
        add("CLN-013", "none", invs, note=note)
    return out


if __name__ == "__main__":
    cs = cases()
    Path(__file__).with_name("cases.json").write_text(
        json.dumps({"patients": PATIENTS, "npis": NPIS, "cases": cs}, indent=1)
    )
    from collections import Counter

    print(Counter((c["rule"], c["expect"]) for c in cs))
