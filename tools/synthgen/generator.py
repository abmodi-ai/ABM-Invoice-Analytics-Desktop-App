"""Synthetic invoice/claim generator with labelled injected duplicates and legitimate repeats.

The injected duplicate types come from the implementation plan (Phase 0), not from what the
rules happen to detect:
  exact_resubmit, ocr_typo, renumbered_rebill, split, rebill_no_freq, mue_overage, ptp_pair
plus the remaining catalogue scenarios: same_file_twice, same_total_same_date, global_period,
frequency_limit, semantic_duplicate, vendor_alias, split_header_only.
Legitimate repeats (must NOT be flagged at PROBABLE or above):
  therapy_series, bilateral, repeat_76, repeat_91, corrected_claim, void_claim, credit_rebill,
  recurring_monthly, dialysis_series.
Everything is driven by a seeded random.Random, so output is reproducible.
"""

from __future__ import annotations

import datetime as dt
import random
import string
from dataclasses import dataclass, field
from typing import Any

from invoice_analytics.normalize.identifiers import npi_check_digit

FIRST = [
    "James",
    "Mary",
    "Robert",
    "Patricia",
    "John",
    "Jennifer",
    "Michael",
    "Linda",
    "William",
    "Elizabeth",
    "David",
    "Barbara",
    "Richard",
    "Susan",
    "Joseph",
    "Jessica",
    "Thomas",
    "Sarah",
    "Charles",
    "Karen",
    "Daniel",
    "Nancy",
    "Matthew",
    "Lisa",
    "Anthony",
    "Betty",
    "Mark",
    "Margaret",
    "Donald",
    "Sandra",
    "Steven",
    "Ashley",
    "Paul",
    "Kimberly",
    "Andrew",
    "Emily",
    "Joshua",
    "Donna",
    "Kenneth",
    "Michelle",
    "Kevin",
    "Carol",
    "Brian",
    "Amanda",
    "George",
    "Melissa",
    "Timothy",
    "Deborah",
    "Ronald",
    "Stephanie",
    "Jose",
    "Rebecca",
    "Luis",
    "Sharon",
    "Carlos",
    "Laura",
    "Juan",
    "Cynthia",
    "Wei",
    "Kathleen",
    "Priya",
    "Amy",
    "Ahmed",
    "Angela",
    "Olga",
    "Shirley",
    "Hiroshi",
    "Anna",
    "Tunde",
    "Brenda",
]
LAST = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Nguyen",
    "Hill",
    "Flores",
    "Green",
    "Adams",
    "Nelson",
    "Baker",
    "Hall",
    "Rivera",
    "Campbell",
    "Mitchell",
    "Carter",
    "Roberts",
    "Okafor",
    "Kowalski",
    "Chen",
    "Patel",
    "Singh",
    "Kim",
    "Yamamoto",
    "Ivanova",
    "Haddad",
    "Mbeki",
    "O'Brien",
    "McAllister",
    "Van der Berg",
    "De la Cruz",
    "Schmidt",
    "Rossi",
    "Dubois",
    "Novak",
    "Silva",
    "Costa",
]
NICK = {
    "William": "Bill",
    "Robert": "Bob",
    "Richard": "Rick",
    "James": "Jim",
    "Michael": "Mike",
    "Elizabeth": "Liz",
    "Jennifer": "Jen",
    "Margaret": "Peggy",
    "Thomas": "Tom",
    "Joseph": "Joe",
    "Daniel": "Dan",
    "Anthony": "Tony",
    "Steven": "Steve",
    "Kenneth": "Ken",
    "Timothy": "Tim",
    "Deborah": "Debbie",
    "Patricia": "Pat",
    "Rebecca": "Becky",
    "Matthew": "Matt",
    "Andrew": "Andy",
}

CODES: dict[str, dict[str, Any]] = {
    "97110": {"fee": 4500, "desc": "Therapeutic exercise each 15 min", "timed": True, "family": "therapy"},
    "97140": {"fee": 4000, "desc": "Manual therapy techniques each 15 min", "timed": True, "family": "therapy"},
    "97530": {"fee": 4800, "desc": "Therapeutic activities each 15 min", "timed": True, "family": "therapy"},
    "97112": {"fee": 4600, "desc": "Neuromuscular reeducation each 15 min", "timed": True, "family": "therapy"},
    "97116": {"fee": 4000, "desc": "Gait training therapy", "timed": True, "family": "therapy"},
    "99213": {"fee": 11000, "desc": "Office visit est pt level 3", "family": "em"},
    "99214": {"fee": 16500, "desc": "Office visit est pt level 4", "family": "em"},
    "99203": {"fee": 15000, "desc": "Office visit new pt level 3", "family": "em"},
    "99204": {"fee": 23000, "desc": "Office visit new pt level 4", "family": "em"},
    "80053": {"fee": 4000, "desc": "Comprehensive metabolic panel", "family": "lab"},
    "85025": {"fee": 2000, "desc": "Complete blood count w auto diff", "family": "lab"},
    "80048": {"fee": 3000, "desc": "Basic metabolic panel", "family": "lab"},
    "36415": {"fee": 1000, "desc": "Routine venipuncture", "family": "lab"},
    "71046": {"fee": 8000, "desc": "Chest x-ray 2 views", "family": "imaging"},
    "73721": {"fee": 40000, "desc": "MRI lower extremity joint w/o contrast", "family": "imaging"},
    "20610": {"fee": 12000, "desc": "Arthrocentesis major joint", "family": "proc"},
    "20604": {"fee": 13000, "desc": "Arthrocentesis small joint w ultrasound", "family": "proc"},
    "29881": {"fee": 180000, "desc": "Knee arthroscopy meniscectomy", "family": "surgery"},
    "10060": {"fee": 18000, "desc": "Incision and drainage of abscess", "family": "surgery"},
    "J1100": {"fee": 500, "desc": "Dexamethasone sodium phosphate 1 mg inj", "family": "drug"},
    "96372": {"fee": 3000, "desc": "Therapeutic injection IM or subq", "family": "drug"},
    "90935": {"fee": 30000, "desc": "Hemodialysis one evaluation", "family": "dialysis"},
    "G0439": {"fee": 14000, "desc": "Annual wellness visit subsequent", "family": "awv"},
}
BASE_POOLS = {
    "therapy": ["97110", "97140", "97112", "97116"],
    "clinic": ["99213", "99214", "99203", "99204", "85025", "80053", "71046", "96372"],
    "ortho": ["99213", "99214", "73721", "20610"],
    "lab": ["80053", "85025", "80048"],
}
SUPPLY_ITEMS = [
    "Exam gloves nitrile (case)",
    "Surgical masks (box)",
    "Printer toner",
    "Janitorial services",
    "IT support retainer",
    "Linen service",
    "Medical waste pickup",
    "Copier lease",
    "Sterilization pouches",
    "Office supplies",
]
OCR_SWAP = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "O": "0", "I": "1", "S": "5", "B": "8"}


def make_npi(rng: random.Random) -> str:
    first9 = str(rng.randint(1, 2)) + "".join(rng.choice(string.digits) for _ in range(8))
    return first9 + npi_check_digit(first9)


@dataclass
class Vendor:
    code: str
    name: str
    alias_name: str
    tax_id: str
    npi: str
    address: str
    phone: str
    kind: str  # therapy | clinic | ortho | lab | supply | dialysis
    providers: list[str]
    prefix: str
    next_num: int
    fmt: str  # X12 | CSV
    party_type: str = "VENDOR"


@dataclass
class Person:
    pid: int
    first: str
    last: str
    dob: str
    sex: str
    zip: str
    member_id: str


@dataclass
class Line:
    patient: Person | None
    dos: str
    code: str | None
    units: float
    charge: int
    npi: str | None
    mods: list[str] = field(default_factory=list)
    desc: str = ""
    patient_override: dict[str, str] | None = None  # identity variant for this record


@dataclass
class Invoice:
    vendor: Vendor
    number: str
    date: str
    lines: list[Line]
    total: int | None = None
    freq: str | None = None
    orig_ref: str | None = None
    status: str = "OPEN"
    party_name_override: str | None = None
    party_address_override: str | None = None
    no_tax_id: bool = False
    tag: str | None = None  # truth tag

    @property
    def amount(self) -> int:
        return self.total if self.total is not None else sum(li.charge for li in self.lines)

    def key(self) -> str:
        return f"{self.number}|{self.date}|{self.amount}"


@dataclass
class Case:
    case_id: str
    kind: str
    duplicate: bool
    invoices: list[str]  # invoice keys of the duplicate / legit copies
    originals: list[str] = field(default_factory=list)
    lines: list[tuple[str, int]] = field(default_factory=list)  # (invoice key, line_no)
    expected_min_tier: str = "PROBABLE"
    expected_rules: list[str] = field(default_factory=list)
    identity_variant: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class World:
    seed: int
    vendors: list[Vendor]
    patients: list[Person]
    invoices: list[Invoice]
    cases: list[Case]
    same_file_twice: list[str] = field(default_factory=list)  # invoice keys in the twice-ingested file
    identity_truth: list[tuple[int, int]] = field(default_factory=list)  # pairs of source-record ids


class Generator:
    def __init__(
        self,
        seed: int = 42,
        n_vendors: int = 30,
        n_patients: int = 1200,
        n_invoices: int = 2500,
        start: str = "2025-01-06",
        days: int = 540,
        dup_rate: float = 1.0,
    ) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.n_vendors = n_vendors
        self.n_patients = n_patients
        self.n_invoices = n_invoices
        self.start = dt.date.fromisoformat(start)
        self.days = days
        self.dup_rate = dup_rate
        self.used_slots: set[tuple[int, str, str]] = set()  # (patient, dos, code)
        self.patient_days: dict[tuple[int, str], set[str]] = {}  # (patient, vendor) -> dos set
        self.totals_by_vendor_date: set[tuple[str, str, int]] = set()
        self.cases: list[Case] = []
        self._case_n = 0

    # ------------------------------------------------------------ entities
    def _vendor(self, i: int, kind: str) -> Vendor:
        r = self.rng
        stem = r.choice(
            [
                "Summit",
                "Riverside",
                "Lakeview",
                "Northgate",
                "Harbor",
                "Cedar",
                "Pinnacle",
                "Metro",
                "Valley",
                "Beacon",
                "Evergreen",
                "Horizon",
                "Keystone",
                "Liberty",
                "Oakwood",
                "Prairie",
            ]
        )
        noun = {
            "therapy": ["Physical Therapy", "Rehab Associates", "Sports Rehab"],
            "clinic": ["Medical Group", "Family Medicine", "Primary Care"],
            "ortho": ["Orthopedic Associates", "Bone & Joint Center"],
            "lab": ["Diagnostic Laboratories", "Clinical Labs"],
            "supply": ["Medical Supply", "Facility Services", "Office Solutions"],
            "dialysis": ["Dialysis Center", "Kidney Care"],
        }[kind]
        n = r.choice(noun)
        suffix = r.choice(["LLC", "Inc", "PC", "PA", "Corp", ""])
        name = f"{stem} {n} {suffix}".strip() + (f" {i}" if i >= 16 else "")
        abbrev = (
            name.replace("Medical", "Med")
            .replace("Center", "Ctr")
            .replace("Associates", "Assoc")
            .replace(" LLC", "")
            .replace(" Inc", " Incorporated")
        )
        tax = f"{r.randint(10, 99)}-{r.randint(1000000, 9999999)}"
        addr = (
            f"{r.randint(100, 9999)} {r.choice(['Main', 'Oak', 'Maple', 'Park', 'Elm', 'Lake'])} "
            f"{r.choice(['Street', 'Ave', 'Blvd', 'Road'])} Suite {r.randint(100, 500)}, Springfield IL 62{r.randint(100, 999)}"
        )
        providers = [make_npi(r) for _ in range(r.randint(2, 5))]
        prefix = r.choice(["INV-", "", "", "A", f"{stem[:3].upper()}-"])
        fmt = "X12" if kind in ("clinic", "ortho") and r.random() < 0.4 else "CSV"
        return Vendor(
            f"V{i:03d}",
            name,
            abbrev,
            tax,
            make_npi(r),
            addr,
            f"217-555-{r.randint(1000, 9999)}",
            kind,
            providers,
            prefix,
            r.randint(1000, 90000),
            fmt,
        )

    def _person(self, i: int) -> Person:
        r = self.rng
        dob = dt.date(1935, 1, 1) + dt.timedelta(days=r.randint(0, 365 * 70))
        return Person(
            i,
            r.choice(FIRST),
            r.choice(LAST),
            dob.isoformat(),
            r.choice("MF"),
            f"62{r.randint(100, 999)}",
            f"M{r.randint(10000000, 99999999)}",
        )

    def _num(self, v: Vendor) -> str:
        v.next_num += self.rng.randint(1, 3)
        return f"{v.prefix}{v.next_num:06d}" if self.rng.random() < 0.5 else f"{v.prefix}{v.next_num}"

    def _date(self, lo: int = 0, hi: int | None = None) -> dt.date:
        return self.start + dt.timedelta(days=self.rng.randint(lo, hi if hi is not None else self.days))

    def _case(
        self,
        kind: str,
        duplicate: bool,
        invoices: list[Invoice],
        originals: list[Invoice] | None = None,
        expected_min_tier: str = "PROBABLE",
        expected_rules: list[str] | None = None,
        lines: list[tuple[Invoice, int]] | None = None,
        identity_variant: bool = False,
    ) -> None:
        self._case_n += 1
        tag = f"C{self._case_n:04d}"
        for inv in invoices:
            inv.tag = tag
        for o in originals or []:
            o.tag = o.tag or f"{tag}-orig"
        self.cases.append(
            Case(
                tag,
                kind,
                duplicate,
                [i.key() for i in invoices],
                [o.key() for o in (originals or [])],
                [(i.key(), n) for i, n in (lines or [])],
                expected_min_tier,
                expected_rules or [],
                identity_variant,
            )
        )

    # ------------------------------------------------------------ baseline
    def _free_dos(self, p: Person, v: Vendor, around: dt.date, codes: list[str]) -> str | None:
        for shift in range(0, 40):
            d = (around + dt.timedelta(days=shift)).isoformat()
            days = self.patient_days.setdefault((p.pid, v.code), set())
            near = any(abs((dt.date.fromisoformat(x) - dt.date.fromisoformat(d)).days) < 2 for x in days)
            if near or any((p.pid, d, c) in self.used_slots for c in codes):
                continue
            return d
        return None

    def _take(self, p: Person, v: Vendor, d: str, codes: list[str]) -> None:
        self.patient_days.setdefault((p.pid, v.code), set()).add(d)
        for c in codes:
            self.used_slots.add((p.pid, d, c))

    def _clinical_invoice(self, v: Vendor, panel: list[Person], when: dt.date) -> Invoice | None:
        r = self.rng
        pool = BASE_POOLS.get(v.kind, BASE_POOLS["clinic"])
        n_pat = 1 if v.fmt == "X12" else r.choice([1, 1, 1, 2, 3])
        lines: list[Line] = []
        for _ in range(n_pat):
            p = r.choice(panel)
            k = r.randint(1, min(3, len(pool)))
            codes = r.sample(pool, k)
            ems = [c for c in codes if CODES[c]["family"] == "em"]
            for extra in ems[1:]:  # at most one E/M code per visit
                codes.remove(extra)
            if "20610" in codes and "20604" in codes:
                codes.remove("20604")
            if "80053" in codes and "80048" in codes:  # NCCI pair: keep baseline free of unbundling
                codes.remove("80048")
            d = self._free_dos(p, v, when - dt.timedelta(days=r.randint(1, 20)), codes)
            if d is None:
                continue
            self._take(p, v, d, codes)
            npi = r.choice(v.providers)
            for c in codes:
                info = CODES[c]
                units = float(r.randint(1, 2)) if info.get("timed") else 1.0
                lines.append(
                    Line(p, d, c, units, int(info["fee"] * units * r.uniform(0.95, 1.1)), npi, desc=info["desc"])
                )
        if not lines:
            return None
        inv = Invoice(v, self._num(v), when.isoformat(), lines, freq="1" if v.fmt == "X12" else None)
        return self._unique_total(inv)

    def _unique_total(self, inv: Invoice) -> Invoice:
        # keep baseline totals unique per vendor per date so INV-003 only fires where injected
        while (inv.vendor.code, inv.date, inv.amount) in self.totals_by_vendor_date:
            inv.lines[-1].charge += 1
        self.totals_by_vendor_date.add((inv.vendor.code, inv.date, inv.amount))
        return inv

    def _supply_invoice(self, v: Vendor, when: dt.date) -> Invoice:
        r = self.rng
        items = r.sample(SUPPLY_ITEMS, r.randint(1, 3))
        lines = [
            Line(None, when.isoformat(), None, float(r.randint(1, 10)), r.randint(2000, 90000), None, desc=i)
            for i in items
        ]
        return self._unique_total(Invoice(v, self._num(v), when.isoformat(), lines))

    # ------------------------------------------------------------ build
    def build(self) -> World:
        r = self.rng
        kinds = ["therapy"] * 6 + ["clinic"] * 8 + ["ortho"] * 4 + ["lab"] * 4 + ["supply"] * 6 + ["dialysis"] * 2
        vendors = [self._vendor(i, kinds[i % len(kinds)]) for i in range(self.n_vendors)]
        patients = [self._person(i) for i in range(self.n_patients)]
        panels = {v.code: r.sample(patients, min(len(patients), r.randint(40, 120))) for v in vendors}
        invoices: list[Invoice] = []
        clinical = [v for v in vendors if v.kind in ("therapy", "clinic", "ortho", "lab")]
        supply = [v for v in vendors if v.kind == "supply"]
        for _ in range(self.n_invoices):
            if supply and r.random() < 0.15:
                invoices.append(self._supply_invoice(r.choice(supply), self._date()))
            else:
                v = r.choice(clinical)
                inv = self._clinical_invoice(v, panels[v.code], self._date(21))
                if inv:
                    invoices.append(inv)
        self._inject(vendors, patients, panels, invoices)
        invoices.sort(key=lambda i: (i.date, i.number))
        sft = [
            i.key()
            for i in invoices
            if i.tag and any(c.case_id == i.tag and c.kind == "same_file_twice" for c in self.cases)
        ]
        return World(self.seed, vendors, patients, invoices, self.cases, sft)

    # ------------------------------------------------------------ injections
    def _pick(self, invoices: list[Invoice], pred: Any) -> Invoice | None:
        cutoff = (self.start + dt.timedelta(days=self.days - 60)).isoformat()

        def ok(i: Invoice) -> bool:
            return i.tag is None and i.date < cutoff and bool(pred(i))

        # random probing keeps large worlds O(n); fall back to a scan for rare predicates
        for _ in range(400):
            i = invoices[self.rng.randrange(len(invoices))]
            if ok(i):
                return i
        cands = [i for i in invoices if ok(i)]
        return self.rng.choice(cands) if cands else None

    def _copy_lines(self, inv: Invoice, variant: bool = False) -> list[Line]:
        out = []
        for li in inv.lines:
            nl = Line(li.patient, li.dos, li.code, li.units, li.charge, li.npi, list(li.mods), li.desc)
            if variant and li.patient:
                nl.patient_override = self._identity_variant(li.patient)
            out.append(nl)
        return out

    def _identity_variant(self, p: Person) -> dict[str, str]:
        r = self.rng
        choice = r.choice(["typo", "nickname", "dob_swap", "typo"])
        first, last, dob = p.first, p.last, p.dob
        if choice == "nickname" and p.first in NICK:
            first = NICK[p.first]
        elif choice == "dob_swap":
            d = dt.date.fromisoformat(p.dob)
            if d.day <= 12 and d.day != d.month:
                dob = f"{d.year:04d}-{d.day:02d}-{d.month:02d}"
            else:
                last = last[:-1] + last[-1] * 2
        else:
            if len(last) > 4:
                i = r.randint(1, len(last) - 2)
                last = last[:i] + last[i + 1] + last[i] + last[i + 2 :]
            else:
                last = last + "e"
        return {"first": first, "last": last, "dob": dob}

    def _later(self, d: str, lo: int, hi: int) -> str:
        return (dt.date.fromisoformat(d) + dt.timedelta(days=self.rng.randint(lo, hi))).isoformat()

    def _inject(
        self, vendors: list[Vendor], patients: list[Person], panels: dict[str, list[Person]], invoices: list[Invoice]
    ) -> None:
        r = self.rng
        scale = self.dup_rate
        n = max(1, int(self.n_invoices / 250))  # cases per type, scaled with data size

        def count(base: int) -> int:
            return max(1, int(base * n * scale))

        clinical = lambda i: i.lines and i.lines[0].patient is not None  # noqa: E731
        header_ok = lambda i: i.vendor.kind == "supply"  # noqa: E731
        # 1. exact resubmission (same number, same lines, later invoice date)
        for k in range(count(4)):
            o = self._pick(invoices, clinical)
            if not o:
                break
            variant = k % 4 == 3
            d = Invoice(o.vendor, o.number, self._later(o.date, 14, 45), self._copy_lines(o, variant), freq=o.freq)
            invoices.append(d)
            self._case("exact_resubmit", True, [d], [o], "HARD", ["INV-001", "CLN-001"], identity_variant=variant)
        # 2. OCR typo on a header-only AP invoice (scanned document re-keyed)
        for _ in range(count(3)):
            o = self._pick(invoices, header_ok)
            if not o:
                break
            num = list(o.number)
            idxs = [i for i, ch in enumerate(num) if ch in OCR_SWAP]
            if not idxs:
                continue
            i = r.choice(idxs)
            num[i] = OCR_SWAP[num[i]]
            d = Invoice(
                o.vendor,
                "".join(num),
                self._later(o.date, 5, 30),
                self._copy_lines(o),
                total=o.amount + r.choice([0, 0, 1, -2]),
            )
            invoices.append(d)
            self._case("ocr_typo", True, [d], [o], "PROBABLE", ["INV-004"])
        # 3. renumbered rebill (new number, same lines)
        for k in range(count(4)):
            o = self._pick(invoices, clinical)
            if not o:
                break
            variant = k % 4 == 3
            d = Invoice(
                o.vendor, self._num(o.vendor), self._later(o.date, 20, 60), self._copy_lines(o, variant), freq=o.freq
            )
            invoices.append(d)
            self._case("renumbered_rebill", True, [d], [o], "HARD", ["INV-006", "CLN-001"], identity_variant=variant)
        # 4. split: lines of one invoice re-billed across 2-3 new invoices
        for _ in range(count(2)):
            o = self._pick(invoices, lambda i: clinical(i) and len(i.lines) >= 2 and i.vendor.fmt == "CSV")
            if not o:
                break
            parts = min(len(o.lines), r.choice([2, 3]))
            ls = self._copy_lines(o)
            chunks = [ls[i::parts] for i in range(parts)]
            news = [Invoice(o.vendor, self._num(o.vendor), self._later(o.date, 3, 25), c) for c in chunks]
            invoices.extend(news)
            self._case("split", True, news, [o], "HARD", ["CLN-001", "INV-008"])
        # 4b. split on header-only invoices: only INV-008 can see it (WEAK)
        for _ in range(count(2)):
            o = self._pick(invoices, lambda i: header_ok(i) and i.amount > 10000)
            if not o:
                break
            a = r.randint(int(o.amount * 0.3), int(o.amount * 0.6))
            news = [
                Invoice(
                    o.vendor,
                    self._num(o.vendor),
                    self._later(o.date, 2, 12),
                    [Line(None, o.date, None, 1, a, None, desc=o.lines[0].desc)],
                ),
                Invoice(
                    o.vendor,
                    self._num(o.vendor),
                    self._later(o.date, 13, 25),
                    [Line(None, o.date, None, 1, o.amount - a, None, desc=o.lines[0].desc)],
                ),
            ]
            invoices.extend(news)
            self._case("split_header_only", True, news, [o], "WEAK", ["INV-008"])
        # 5. rebill without frequency code: X12 claim paid, re-sent as a new original claim by another
        #    provider in the group (so it is not an exact duplicate line)
        for _ in range(count(3)):
            o = self._pick(invoices, lambda i: clinical(i) and i.vendor.fmt == "X12" and len(i.vendor.providers) > 1)
            if not o:
                break
            o.status = "PAID"
            ls = self._copy_lines(o)
            other = [p for p in o.vendor.providers if p != ls[0].npi][0]
            for li in ls:
                li.npi = other
            d = Invoice(o.vendor, self._num(o.vendor), self._later(o.date, 30, 70), ls, freq="1")
            invoices.append(d)
            self._case("rebill_no_freq", True, [d], [o], "PROBABLE", ["CLN-009"])
        # 6. MUE overage: extra units of a timed code on the same patient/date across a second invoice
        therapy = [v for v in vendors if v.kind == "therapy"]
        for _ in range(count(3)):
            if not therapy:
                break
            v = r.choice(therapy)
            p = r.choice(panels[v.code])
            when = self._date(21, self.days - 60)
            d0 = self._free_dos(p, v, when, ["97110"])
            if not d0:
                continue
            self._take(p, v, d0, ["97110"])
            npi = v.providers[0]
            a = Invoice(
                v,
                self._num(v),
                (dt.date.fromisoformat(d0) + dt.timedelta(days=3)).isoformat(),
                [Line(p, d0, "97110", 4, 18000, npi, desc=CODES["97110"]["desc"])],
            )
            b = Invoice(
                v,
                self._num(v),
                (dt.date.fromisoformat(d0) + dt.timedelta(days=17)).isoformat(),
                [Line(p, d0, "97110", 4, 18000, npi, ["GP"], desc=CODES["97110"]["desc"])],
            )
            invoices.extend([self._unique_total(a), self._unique_total(b)])
            self._case("mue_overage", True, [b], [a], "PROBABLE", ["CLN-004"])
        # 7. NCCI PTP pair unbundled on one claim
        pairs = [("20610", "20604", []), ("97140", "97530", [])]
        ortho = [v for v in vendors if v.kind in ("ortho", "therapy")]
        for k in range(count(3)):
            if not ortho:
                break
            v = r.choice(ortho)
            c1, c2, mods = pairs[k % 2] if v.kind == "therapy" else pairs[0]
            if v.kind == "therapy":
                c1, c2 = "97140", "97530"
            p = r.choice(panels[v.code])
            d0 = self._free_dos(p, v, self._date(21, self.days - 60), [c1, c2])
            if not d0:
                continue
            self._take(p, v, d0, [c1, c2])
            npi = v.providers[0]
            inv = Invoice(
                v,
                self._num(v),
                self._later(d0, 2, 10),
                [
                    Line(p, d0, c1, 1, CODES[c1]["fee"], npi, desc=CODES[c1]["desc"]),
                    Line(p, d0, c2, 1, CODES[c2]["fee"], npi, list(mods), desc=CODES[c2]["desc"]),
                ],
            )
            invoices.append(self._unique_total(inv))
            self._case("ptp_pair", True, [inv], [], "PROBABLE", ["CLN-005"], lines=[(inv, 2)])
        # 8. same total and date, different number (typically a re-keyed invoice)
        for _ in range(count(2)):
            o = self._pick(invoices, header_ok)
            if not o:
                break
            d = Invoice(o.vendor, self._num(o.vendor), o.date, self._copy_lines(o))
            invoices.append(d)
            self.totals_by_vendor_date.add((o.vendor.code, o.date, o.amount))
            self._case("same_total_same_date", True, [d], [o], "PROBABLE", ["INV-003"])
        # 9. global period: E/M by the surgeon 30 days after knee arthroscopy without modifier 24
        orthos = [v for v in vendors if v.kind == "ortho"]
        for _ in range(count(2)):
            if not orthos:
                break
            v = r.choice(orthos)
            p = r.choice(panels[v.code])
            s_day = self._date(21, self.days - 120)
            ds = self._free_dos(p, v, s_day, ["29881"])
            if not ds:
                continue
            de = self._free_dos(p, v, dt.date.fromisoformat(ds) + dt.timedelta(days=30), ["99213"])
            if not de:
                continue
            self._take(p, v, ds, ["29881"])
            self._take(p, v, de, ["99213"])
            npi = v.providers[0]
            a = Invoice(
                v,
                self._num(v),
                self._later(ds, 1, 5),
                [Line(p, ds, "29881", 1, 180000, npi, ["RT"], CODES["29881"]["desc"])],
            )
            b = Invoice(
                v,
                self._num(v),
                self._later(de, 1, 5),
                [Line(p, de, "99213", 1, 11000, npi, desc=CODES["99213"]["desc"])],
            )
            invoices.extend([self._unique_total(a), self._unique_total(b)])
            self._case("global_period", True, [b], [a], "PROBABLE", ["CLN-006"])
        # 10. frequency limit: second annual wellness visit in the same year
        clinics = [v for v in vendors if v.kind == "clinic" and v.fmt == "CSV"]
        for _ in range(count(2)):
            if not clinics:
                break
            v = r.choice(clinics)
            p = r.choice(panels[v.code])
            d1 = self._free_dos(p, v, dt.date(self.start.year, 2, 1) + dt.timedelta(days=r.randint(0, 60)), ["G0439"])
            if not d1:
                continue
            d2 = self._free_dos(p, v, dt.date.fromisoformat(d1) + dt.timedelta(days=r.randint(90, 200)), ["G0439"])
            if not d2 or d2[:4] != d1[:4]:
                continue
            self._take(p, v, d1, ["G0439"])
            self._take(p, v, d2, ["G0439"])
            a = Invoice(
                v,
                self._num(v),
                self._later(d1, 1, 5),
                [Line(p, d1, "G0439", 1, 14000, v.providers[0], desc=CODES["G0439"]["desc"])],
            )
            b = Invoice(
                v,
                self._num(v),
                self._later(d2, 1, 5),
                [Line(p, d2, "G0439", 1, 14000, v.providers[1 % len(v.providers)], desc=CODES["G0439"]["desc"])],
            )
            invoices.extend([self._unique_total(a), self._unique_total(b)])
            self._case("frequency_limit", True, [b], [a], "PROBABLE", ["CLN-007"])
        # 11. semantic duplicate: same service re-billed without a code on a new invoice
        for _ in range(count(2)):
            o = self._pick(invoices, lambda i: clinical(i) and i.vendor.fmt == "CSV")
            if not o:
                break
            li = o.lines[0]
            nl = Line(
                li.patient,
                li.dos,
                None,
                li.units,
                li.charge + 7,
                li.npi,
                desc=li.desc.replace("each", "ea").replace("minutes", "min"),
            )
            d = Invoice(o.vendor, self._num(o.vendor), self._later(o.date, 10, 40), [nl])
            invoices.append(self._unique_total(d))
            self._case("semantic_duplicate", True, [d], [o], "WEAK", ["CLN-008"])
        # 12. vendor alias: same invoice sent under a different trading name, same remit address, no tax ID
        for _ in range(count(2)):
            o = self._pick(invoices, header_ok)
            if not o:
                break
            d = Invoice(
                o.vendor,
                o.number,
                self._later(o.date, 7, 30),
                self._copy_lines(o),
                party_name_override=f"{o.vendor.name.split()[0]} {o.vendor.code} Holdings Group",
                no_tax_id=True,
            )
            invoices.append(d)
            self._case("vendor_alias", True, [d], [o], "HARD", ["INV-007"])
        # 13. same file ingested twice (one whole batch file re-imported)
        for _ in range(1):
            o = self._pick(invoices, lambda i: i.vendor.fmt == "CSV" and clinical(i))
            if not o:
                break
            self._case("same_file_twice", True, [o], [], "HARD", ["INV-002"])
        self._legit(vendors, panels, invoices)

    def _legit(self, vendors: list[Vendor], panels: dict[str, list[Person]], invoices: list[Invoice]) -> None:
        r = self.rng
        n = max(1, int(self.n_invoices / 250))
        therapy = [v for v in vendors if v.kind == "therapy"]
        # therapy series: 3x/week for 4 weeks, 2 units each visit, one invoice per week
        for _ in range(2 * n):
            if not therapy:
                break
            v = r.choice(therapy)
            p = r.choice(panels[v.code])
            monday = self._date(21, self.days - 60)
            monday -= dt.timedelta(days=monday.weekday())
            weeks = []
            ok = True
            for w in range(4):
                days = [(monday + dt.timedelta(days=7 * w + o)).isoformat() for o in (0, 2, 4)]
                if any((p.pid, d, "97110") in self.used_slots for d in days):
                    ok = False
                    break
                weeks.append(days)
            if not ok:
                continue
            invs = []
            for days in weeks:
                for d in days:
                    self._take(p, v, d, ["97110"])
                ls = [Line(p, d, "97110", 2, 9000, v.providers[0], ["GP"], CODES["97110"]["desc"]) for d in days]
                inv = Invoice(v, self._num(v), self._later(days[-1], 1, 3), ls)
                invs.append(self._unique_total(inv))
            invoices.extend(invs)
            self._case("therapy_series", False, invs)
        # bilateral: 20610-RT and 20610-LT same day
        orthos = [v for v in vendors if v.kind == "ortho"]
        for _ in range(n):
            if not orthos:
                break
            v = r.choice(orthos)
            p = r.choice(panels[v.code])
            d = self._free_dos(p, v, self._date(21, self.days - 30), ["20610"])
            if not d:
                continue
            self._take(p, v, d, ["20610"])
            inv = Invoice(
                v,
                self._num(v),
                self._later(d, 1, 5),
                [
                    Line(p, d, "20610", 1, 12000, v.providers[0], ["RT"], CODES["20610"]["desc"]),
                    Line(p, d, "20610", 1, 12000, v.providers[0], ["LT"], CODES["20610"]["desc"]),
                ],
            )
            invoices.append(self._unique_total(inv))
            self._case("bilateral", False, [inv])
        clinics = [v for v in vendors if v.kind in ("clinic", "lab") and v.fmt == "CSV"]
        # repeat x-ray with 76 / repeat lab with 91
        for kind, code, mod in (("repeat_76", "71046", "76"), ("repeat_91", "80048", "91")):
            for _ in range(n):
                if not clinics:
                    break
                v = r.choice(clinics)
                p = r.choice(panels[v.code])
                d = self._free_dos(p, v, self._date(21, self.days - 30), [code])
                if not d:
                    continue
                self._take(p, v, d, [code])
                inv = Invoice(
                    v,
                    self._num(v),
                    self._later(d, 1, 5),
                    [
                        Line(p, d, code, 1, CODES[code]["fee"], v.providers[0], desc=CODES[code]["desc"]),
                        Line(p, d, code, 1, CODES[code]["fee"], v.providers[0], [mod], CODES[code]["desc"]),
                    ],
                )
                invoices.append(self._unique_total(inv))
                self._case(kind, False, [inv])
        # corrected claim (7) and void (8) on X12 vendors
        for kind, freq in (("corrected_claim", "7"), ("void_claim", "8")):
            for _ in range(n):
                o = self._pick(invoices, lambda i: i.vendor.fmt == "X12" and i.lines and i.lines[0].patient)
                if not o:
                    break
                ls = self._copy_lines(o)
                if freq == "7":
                    ls[0].charge += 500
                d = Invoice(
                    o.vendor, self._num(o.vendor), self._later(o.date, 10, 30), ls, freq=freq, orig_ref=o.number
                )
                o.tag = o.tag or "legit-original"
                invoices.append(d)
                self._case(kind, False, [d], [o])
        # credit memo then rebill
        for _ in range(n):
            o = self._pick(invoices, lambda i: i.vendor.fmt == "CSV" and i.lines and i.lines[0].patient)
            if not o:
                break
            o.tag = "legit-original"
            cr_lines = [
                Line(li.patient, li.dos, li.code, li.units, -li.charge, li.npi, list(li.mods), li.desc)
                for li in o.lines
            ]
            cr = Invoice(o.vendor, f"{o.number}-CR", self._later(o.date, 5, 10), cr_lines, status="CREDIT")
            rb = Invoice(o.vendor, self._num(o.vendor), self._later(cr.date, 1, 10), self._copy_lines(o))
            invoices.extend([cr, rb])
            self._case("credit_rebill", False, [cr, rb], [o])
        # recurring monthly contract invoice with the same amount (AP, header only)
        supply = [v for v in vendors if v.kind == "supply"]
        for _ in range(n):
            if not supply:
                break
            v = r.choice(supply)
            amt = r.randint(100000, 300000)
            d0 = self._date(0, 60)
            invs = []
            for m in range(6):
                d = (d0 + dt.timedelta(days=30 * m)).isoformat()
                invs.append(
                    Invoice(v, self._num(v), d, [Line(None, d, None, 1, amt, None, desc="Monthly service contract")])
                )
            invoices.extend(invs)
            self._case("recurring_monthly", False, invs)
        # dialysis 3x/week
        dial = [v for v in vendors if v.kind == "dialysis"]
        for _ in range(n):
            if not dial:
                break
            v = r.choice(dial)
            p = r.choice(panels[v.code])
            monday = self._date(21, self.days - 60)
            monday -= dt.timedelta(days=monday.weekday())
            days = [(monday + dt.timedelta(days=7 * w + o)).isoformat() for w in range(2) for o in (0, 2, 4)]
            if any((p.pid, d, "90935") in self.used_slots for d in days):
                continue
            for d in days:
                self._take(p, v, d, ["90935"])
            inv = Invoice(
                v,
                self._num(v),
                self._later(days[-1], 1, 3),
                [Line(p, d, "90935", 1, 30000, v.providers[0], desc=CODES["90935"]["desc"]) for d in days],
            )
            invoices.append(self._unique_total(inv))
            self._case("dialysis_series", False, [inv])
