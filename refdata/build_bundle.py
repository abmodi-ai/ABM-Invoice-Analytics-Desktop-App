"""Build a signed reference-data bundle (.vref) from CMS source files.

Runs on an internet-connected BUILD machine, never on client installs. Typical quarterly run:

    uv run python refdata/build_bundle.py \\
        --ptp-practitioner ncci_ptp_practitioner_2026q3.zip \\
        --ptp-hospital     ncci_ptp_hospital_2026q3.zip \\
        --mue-practitioner mue_practitioner_2026q3.csv \\
        --mue-hospital     mue_hospital_2026q3.csv \\
        --pfs-rvu          PPRRVU26_JUL.csv \\
        --hcpcs            HCPC2026_JUL_ANWEB.txt \\
        --frequency-limits client/frequency_limits.csv \\
        --recurring        client/recurring_series.csv \\
        --version 2026Q3 --effective-from 2026-07-01 --effective-to 2026-09-30 \\
        --key refdata/keys/release-signing.private --out refdata/out/verismo-refdata-2026Q3.vref

`fetch_cms.py` downloads the published files (URLs are passed in because CMS moves them each
quarter). Column detection is header-based so small CMS layout changes are tolerated; anything
unrecognized fails loudly rather than producing an empty dataset.

Licensing: CMS NCCI/MUE/PFS/HCPCS Level II files are public. CPT descriptors are AMA-licensed
and are deliberately NOT included (decision D6); codes work without descriptors.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from verismo_engine.clinical.refdata import build_bundle  # noqa: E402


class SourceError(ValueError):
    pass


def _iter_text_files(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (name, text) for a plain file or every .txt/.csv inside a zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.lower().endswith((".txt", ".csv")):
                    yield n, z.read(n).decode("latin-1")
    elif path.suffix.lower() in (".xlsx", ".xls"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        buf = io.StringIO()
        w = csv.writer(buf)
        for r in wb.worksheets[0].iter_rows(values_only=True):
            w.writerow(["" if v is None else v for v in r])
        yield path.name, buf.getvalue()
    else:
        yield path.name, path.read_text(encoding="latin-1")


def _rows_with_header(text: str, must_have: list[str]) -> Iterator[dict[str, str]]:
    """CMS files often have preamble/disclaimer lines before the header; find the header row."""
    sample = text[:20000]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    lines = text.splitlines()
    for i, line in enumerate(lines[:200]):
        low = line.lower()
        if all(m in low for m in must_have):
            reader = csv.reader(lines[i:], delimiter=delim)
            header = [re.sub(r"\s+", " ", h).strip().lower() for h in next(reader)]
            for r in reader:
                if r and any(c.strip() for c in r):
                    yield dict(zip(header, (c.strip() for c in r), strict=False))
            return
    raise SourceError(f"header with {must_have} not found")


def _col(row: dict[str, str], *needles: str) -> str:
    for k, v in row.items():
        if all(n in k for n in needles):
            return v
    return ""


def _ymd(v: str) -> str | None:
    v = v.strip()
    if not v or v in ("*", "0"):
        return None
    if re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def parse_ptp(path: Path) -> list[dict[str, Any]]:
    out = []
    for _name, text in _iter_text_files(path):
        for r in _rows_with_header(text, ["column 1", "column 2"]):
            c1, c2 = _col(r, "column 1").upper(), _col(r, "column 2").upper()
            if not re.fullmatch(r"[0-9A-Z]{5}", c1) or not re.fullmatch(r"[0-9A-Z]{5}", c2):
                continue
            mi = _col(r, "modifier")[:1]
            out.append(
                {
                    "column1_code": c1,
                    "column2_code": c2,
                    "effective_from": _ymd(_col(r, "effective")) or "1996-01-01",
                    "effective_to": _ymd(_col(r, "deletion")),
                    "modifier_indicator": mi if mi in "019" else "9",
                }
            )
    if not out:
        raise SourceError(f"{path.name}: no PTP rows recognized")
    return out


def parse_mue(path: Path, effective_from: str) -> list[dict[str, Any]]:
    out = []
    for _name, text in _iter_text_files(path):
        for r in _rows_with_header(text, ["mue"]):
            code = (_col(r, "hcpcs") or _col(r, "cpt")).upper()
            val = _col(r, "mue value") or _col(r, "mue values")
            mai = re.match(r"\s*(\d)", _col(r, "adjudication indicator") or _col(r, "mai") or "")
            if not re.fullmatch(r"[0-9A-Z]{5}", code) or not val.strip().isdigit() or not mai:
                continue
            out.append(
                {"code": code, "mue_value": int(val), "mai": int(mai.group(1)), "effective_from": effective_from}
            )
    if not out:
        raise SourceError(f"{path.name}: no MUE rows recognized")
    return out


def parse_pfs_global(path: Path, year: int) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    for _name, text in _iter_text_files(path):
        for r in _rows_with_header(text, ["hcpcs", "glob"]):
            code = _col(r, "hcpcs").upper()
            mod = _col(r, "mod")
            g = _col(r, "glob").upper()
            if (
                mod
                or not re.fullmatch(r"[0-9A-Z]{5}", code)
                or g not in ("000", "010", "090", "XXX", "YYY", "ZZZ", "MMM")
            ):
                continue
            seen.setdefault(code, g)
    if not seen:
        raise SourceError(f"{path.name}: no global-days rows recognized")
    return [{"code": c, "global_days": g, "effective_year": year} for c, g in sorted(seen.items())]


def parse_hcpcs(path: Path) -> list[dict[str, Any]]:
    """HCPCS Level II annual file: fixed-width (code in cols 1-5, short description ~ cols 92-119)."""
    out = []
    for _name, text in _iter_text_files(path):
        for line in text.splitlines():
            code = line[:5].strip().upper()
            if re.fullmatch(r"[A-V]\d{4}", code) and line[5:10].strip() in ("", "00001", "00100"):
                desc = line[91:119].strip() if len(line) > 100 else line[10:60].strip()
                out.append({"code": code, "short_desc": desc, "effective_from": None})
    if not out:
        raise SourceError(f"{path.name}: no HCPCS rows recognized")
    return out


def parse_client_csv(path: Path, required: list[str]) -> list[dict[str, Any]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    missing = [c for c in required if rows and c not in rows[0]]
    if missing:
        raise SourceError(f"{path.name}: missing columns {missing}")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ptp-practitioner", type=Path)
    ap.add_argument("--ptp-hospital", type=Path)
    ap.add_argument("--mue-practitioner", type=Path)
    ap.add_argument("--mue-hospital", type=Path)
    ap.add_argument("--mue-dme", type=Path)
    ap.add_argument("--pfs-rvu", type=Path)
    ap.add_argument("--hcpcs", type=Path)
    ap.add_argument("--frequency-limits", type=Path)
    ap.add_argument("--recurring", type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument("--effective-from", required=True)
    ap.add_argument("--effective-to")
    ap.add_argument("--key", type=Path, required=True, help="hex Ed25519 private key file (keep offline)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    year = int(a.effective_from[:4])
    ds: list[dict[str, Any]] = []

    def add(name: str, rows: list[dict[str, Any]]) -> None:
        ds.append(
            {
                "dataset": name,
                "version": a.version,
                "effective_from": a.effective_from,
                "effective_to": a.effective_to,
                "rows": rows,
            }
        )
        print(f"{name}: {len(rows):,} rows")

    if a.ptp_practitioner:
        add("NCCI_PTP_PRACTITIONER", parse_ptp(a.ptp_practitioner))
    if a.ptp_hospital:
        add("NCCI_PTP_HOSPITAL", parse_ptp(a.ptp_hospital))
    if a.mue_practitioner:
        add("MUE_PRACTITIONER", parse_mue(a.mue_practitioner, a.effective_from))
    if a.mue_hospital:
        add("MUE_HOSPITAL", parse_mue(a.mue_hospital, a.effective_from))
    if a.mue_dme:
        add("MUE_DME", parse_mue(a.mue_dme, a.effective_from))
    if a.pfs_rvu:
        add("PFS_GLOBAL", parse_pfs_global(a.pfs_rvu, year))
    if a.hcpcs:
        add("HCPCS", parse_hcpcs(a.hcpcs))
    if a.frequency_limits:
        add("FREQUENCY_LIMITS", parse_client_csv(a.frequency_limits, ["code", "max_count", "period", "scope"]))
    if a.recurring:
        add("RECURRING_SERIES", parse_client_csv(a.recurring, ["code", "typical_frequency"]))
    if not ds:
        ap.error("no source files given")
    blob = build_bundle(ds, a.key.read_text().strip(), a.version)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(blob)
    print(f"wrote {a.out} ({len(blob):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
