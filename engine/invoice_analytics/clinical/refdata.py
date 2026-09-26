"""Signed reference-data bundles (.vref) and effective-dated lookups (spec section 6).

A .vref is a zip:  manifest.json, manifest.sig (Ed25519 over the manifest bytes), data/<file>.csv
The engine verifies the signature with a public key embedded in the app, then every file's
sha256, then loads everything in one transaction.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from invoice_analytics.context import Engine
from invoice_analytics.security import audit

# Public keys trusted for reference-data bundles. The first is the development key whose private
# half lives in refdata/keys/ (gitignored). Production builds replace this list with the release
# signing key; see docs/runbook-refdata.md.
TRUSTED_PUBLIC_KEYS: list[str] = [
    "d0d67fbe84e13e8b240117dc18e306e93be94df5d9ca854c948ea79ef50a5c36",
]

MAX_BUNDLE_BYTES = 512 * 1024 * 1024


class RefdataError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetSpec:
    table: str
    columns: tuple[str, ...]
    fixed: dict[str, Any]
    replace_where: str  # rows removed before load (dataset supersedes previous version)


DATASETS: dict[str, DatasetSpec] = {
    "NCCI_PTP_PRACTITIONER": DatasetSpec(
        "ref_ncci_ptp",
        ("column1_code", "column2_code", "effective_from", "effective_to", "modifier_indicator"),
        {"setting": "PRACTITIONER"},
        "setting='PRACTITIONER'",
    ),
    "NCCI_PTP_HOSPITAL": DatasetSpec(
        "ref_ncci_ptp",
        ("column1_code", "column2_code", "effective_from", "effective_to", "modifier_indicator"),
        {"setting": "HOSPITAL"},
        "setting='HOSPITAL'",
    ),
    "MUE_PRACTITIONER": DatasetSpec(
        "ref_mue",
        ("code", "mue_value", "mai", "effective_from", "effective_to"),
        {"setting": "PRACTITIONER"},
        "setting='PRACTITIONER'",
    ),
    "MUE_HOSPITAL": DatasetSpec(
        "ref_mue",
        ("code", "mue_value", "mai", "effective_from", "effective_to"),
        {"setting": "HOSPITAL"},
        "setting='HOSPITAL'",
    ),
    "MUE_DME": DatasetSpec(
        "ref_mue", ("code", "mue_value", "mai", "effective_from", "effective_to"), {"setting": "DME"}, "setting='DME'"
    ),
    "PFS_GLOBAL": DatasetSpec("ref_global_days", ("code", "global_days", "effective_year"), {}, "1=1"),
    "HCPCS": DatasetSpec("ref_hcpcs", ("code", "short_desc", "effective_from", "effective_to"), {}, "1=1"),
    "FREQUENCY_LIMITS": DatasetSpec(
        "ref_frequency_limits", ("code", "max_count", "period", "scope", "source", "note"), {}, "1=1"
    ),
    "RECURRING_SERIES": DatasetSpec("ref_recurring_series", ("code", "typical_frequency", "note"), {}, "1=1"),
}
VERSIONED_TABLES = {"ref_ncci_ptp", "ref_mue", "ref_global_days", "ref_hcpcs"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_bundle(datasets: list[dict[str, Any]], private_key_hex: str, bundle_version: str) -> bytes:
    """datasets: [{dataset, version, effective_from, effective_to, rows: [dict]}]"""
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    buf = io.BytesIO()
    manifest: dict[str, Any] = {
        "format": "vref/1",
        "bundle_version": bundle_version,
        "created_at": datetime.now(UTC).isoformat(),
        "datasets": [],
    }
    files: dict[str, bytes] = {}
    for ds in datasets:
        spec = DATASETS.get(ds["dataset"])
        if spec is None:
            raise RefdataError(f"unknown dataset {ds['dataset']}")
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=list(spec.columns), extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in ds["rows"]:
            w.writerow({c: r.get(c, "") for c in spec.columns})
        data = out.getvalue().encode()
        fname = f"data/{ds['dataset'].lower()}.csv"
        files[fname] = data
        manifest["datasets"].append(
            {
                "dataset": ds["dataset"],
                "version": ds["version"],
                "effective_from": ds.get("effective_from"),
                "effective_to": ds.get("effective_to"),
                "file": fname,
                "sha256": _sha(data),
                "rows": len(ds["rows"]),
            }
        )
    mbytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", mbytes)
        z.writestr("manifest.sig", key.sign(mbytes).hex())
        for f, d in files.items():
            z.writestr(f, d)
    return buf.getvalue()


def verify_bundle(blob: bytes, trusted: list[str] | None = None) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(blob) > MAX_BUNDLE_BYTES:
        raise RefdataError("bundle too large")
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise RefdataError("not a valid .vref bundle") from e
    names = set(z.namelist())
    if "manifest.json" not in names or "manifest.sig" not in names:
        raise RefdataError("bundle is missing its manifest or signature")
    for n in names:
        if n.startswith("/") or ".." in n.split("/"):
            raise RefdataError(f"unsafe path in bundle: {n}")
    mbytes = z.read("manifest.json")
    sig = bytes.fromhex(z.read("manifest.sig").decode().strip())
    ok = False
    for pk in trusted if trusted is not None else TRUSTED_PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk)).verify(sig, mbytes)
            ok = True
            break
        except InvalidSignature:
            continue
    if not ok:
        raise RefdataError("signature verification failed: bundle is not from a trusted source")
    manifest = json.loads(mbytes)
    files: dict[str, bytes] = {}
    for ds in manifest["datasets"]:
        if ds["dataset"] not in DATASETS:
            raise RefdataError(f"unknown dataset {ds['dataset']}")
        data = z.read(ds["file"])
        if _sha(data) != ds["sha256"]:
            raise RefdataError(f"hash mismatch for {ds['file']}")
        files[ds["file"]] = data
    return manifest, files


def import_bundle(
    eng: Engine, blob: bytes, *, user_id: int | None = None, trusted: list[str] | None = None
) -> dict[str, Any]:
    manifest, files = verify_bundle(blob, trusted)
    ts = datetime.now(UTC).isoformat()
    loaded = []
    with eng.db.tx() as c:
        for ds in manifest["datasets"]:
            spec = DATASETS[ds["dataset"]]
            rows = list(csv.DictReader(io.StringIO(files[ds["file"]].decode())))
            c.execute(f"DELETE FROM {spec.table} WHERE {spec.replace_where}")
            cols = list(spec.columns) + list(spec.fixed)
            if spec.table in VERSIONED_TABLES:
                cols.append("version")
            ph = ",".join("?" * len(cols))
            vals = []
            for r in rows:
                v: list[Any] = [(r.get(col) or None) for col in spec.columns]
                v += list(spec.fixed.values())
                if spec.table in VERSIONED_TABLES:
                    v.append(ds["version"])
                vals.append(v)
            c.executemany(f"INSERT INTO {spec.table}({','.join(cols)}) VALUES ({ph})", vals)
            c.execute(
                "INSERT OR REPLACE INTO ref_dataset_versions(dataset, version, effective_from, effective_to,"
                " sha256, row_count, imported_at, imported_by) VALUES (?,?,?,?,?,?,?,?)",
                (
                    ds["dataset"],
                    ds["version"],
                    ds.get("effective_from"),
                    ds.get("effective_to"),
                    ds["sha256"],
                    len(rows),
                    ts,
                    user_id,
                ),
            )
            loaded.append({"dataset": ds["dataset"], "version": ds["version"], "rows": len(rows)})
        audit.record(
            eng.db,
            "REFDATA_IMPORT",
            user_id=user_id,
            entity_type="refdata",
            entity_id=manifest.get("bundle_version"),
            after={"datasets": loaded},
            conn=c,
        )
    eng.store.reload_refdata()
    return {"bundle_version": manifest.get("bundle_version"), "datasets": loaded}


def installed_versions(eng: Engine) -> list[dict[str, Any]]:
    rows = eng.db.query("SELECT * FROM ref_dataset_versions ORDER BY dataset, imported_at DESC")
    today = datetime.now(UTC).date().isoformat()
    for r in rows:
        r["stale"] = bool(r.get("effective_to") and r["effective_to"] < today)
    return rows


# ---------------------------------------------------------------- effective-dated lookups
def lookup_mue(eng: Engine, code: str, dos: str, setting: str = "PRACTITIONER") -> dict[str, Any] | None:
    return eng.db.one(
        "SELECT code, mue_value, mai, setting, version, effective_from, effective_to FROM ref_mue"
        " WHERE code=? AND setting=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)"
        " ORDER BY effective_from DESC LIMIT 1",
        (code, setting, dos, dos),
    )


def lookup_ptp(eng: Engine, code_a: str, code_b: str, dos: str, setting: str = "PRACTITIONER") -> list[dict[str, Any]]:
    return eng.db.query(
        "SELECT column1_code, column2_code, modifier_indicator, setting, version, effective_from, effective_to"
        " FROM ref_ncci_ptp WHERE ((column1_code=? AND column2_code=?) OR (column1_code=? AND column2_code=?))"
        " AND setting=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
        (code_a, code_b, code_b, code_a, setting, dos, dos),
    )


def lookup_global_days(eng: Engine, code: str, dos: str) -> dict[str, Any] | None:
    return eng.db.one(
        "SELECT code, global_days, effective_year, version FROM ref_global_days WHERE code=? AND effective_year<=?"
        " ORDER BY effective_year DESC LIMIT 1",
        (code, int(dos[:4])),
    )


# ---------------------------------------------------------------- development sample
SAMPLE_DIR = Path(__file__).resolve().parents[3] / "refdata" / "sample"


def dev_sample_datasets() -> list[dict[str, Any]]:
    """Small illustrative reference data for development and tests.

    NOT CMS data: values are chosen to exercise the rules. Production uses bundles built by
    /refdata from the published CMS files.
    """
    ptp = [
        {"column1_code": "97140", "column2_code": "97530", "effective_from": "2020-01-01", "modifier_indicator": "1"},
        {"column1_code": "20610", "column2_code": "20604", "effective_from": "2020-01-01", "modifier_indicator": "0"},
        {"column1_code": "29881", "column2_code": "29880", "effective_from": "2020-01-01", "modifier_indicator": "0"},
        {"column1_code": "93000", "column2_code": "93005", "effective_from": "2020-01-01", "modifier_indicator": "0"},
        {"column1_code": "99214", "column2_code": "36415", "effective_from": "2020-01-01", "modifier_indicator": "1"},
        {"column1_code": "11042", "column2_code": "97597", "effective_from": "2020-01-01", "modifier_indicator": "1"},
        {"column1_code": "80053", "column2_code": "80048", "effective_from": "2020-01-01", "modifier_indicator": "0"},
        {"column1_code": "45380", "column2_code": "45378", "effective_from": "2020-01-01", "modifier_indicator": "0"},
        {
            "column1_code": "97110",
            "column2_code": "97750",
            "effective_from": "2020-01-01",
            "effective_to": "2025-12-31",
            "modifier_indicator": "1",
        },
    ]
    mue = [
        {"code": "97110", "mue_value": 6, "mai": 3, "effective_from": "2020-01-01"},
        {"code": "97140", "mue_value": 4, "mai": 3, "effective_from": "2020-01-01"},
        {"code": "97530", "mue_value": 6, "mai": 3, "effective_from": "2020-01-01"},
        {"code": "20610", "mue_value": 2, "mai": 2, "effective_from": "2020-01-01"},
        {"code": "36415", "mue_value": 2, "mai": 3, "effective_from": "2020-01-01"},
        {"code": "99214", "mue_value": 1, "mai": 2, "effective_from": "2020-01-01"},
        {"code": "99213", "mue_value": 1, "mai": 2, "effective_from": "2020-01-01"},
        {"code": "J1100", "mue_value": 10, "mai": 1, "effective_from": "2020-01-01", "effective_to": "2025-06-30"},
        {"code": "J1100", "mue_value": 20, "mai": 1, "effective_from": "2025-07-01"},
        {"code": "80053", "mue_value": 1, "mai": 2, "effective_from": "2020-01-01"},
        {"code": "71046", "mue_value": 2, "mai": 3, "effective_from": "2020-01-01"},
    ]
    glob = [
        {"code": c, "global_days": g, "effective_year": y}
        for y in (2024, 2025, 2026)
        for c, g in (
            ("29881", "090"),
            ("27447", "090"),
            ("11042", "000"),
            ("20610", "000"),
            ("10060", "010"),
            ("12001", "010"),
            ("45380", "000"),
            ("99213", "XXX"),
            ("99214", "XXX"),
            ("97110", "XXX"),
            ("66984", "090"),
        )
    ]
    hcpcs = [
        {"code": "J1100", "short_desc": "Dexamethasone sodium phos", "effective_from": "2020-01-01"},
        {"code": "A4550", "short_desc": "Surgical trays", "effective_from": "2020-01-01"},
        {"code": "G0283", "short_desc": "Elec stim other than wound", "effective_from": "2020-01-01"},
    ]
    freq = [
        {
            "code": "G0438",
            "max_count": 1,
            "period": "LIFETIME",
            "scope": "PATIENT",
            "source": "sample",
            "note": "Initial annual wellness visit",
        },
        {
            "code": "G0439",
            "max_count": 1,
            "period": "YEAR",
            "scope": "PATIENT",
            "source": "sample",
            "note": "Subsequent annual wellness visit",
        },
        {
            "code": "97110",
            "max_count": 8,
            "period": "WEEK",
            "scope": "PATIENT",
            "source": "sample",
            "note": "Client plan-of-care cap",
        },
        {
            "code": "77067",
            "max_count": 1,
            "period": "YEAR",
            "scope": "PATIENT",
            "source": "sample",
            "note": "Screening mammography",
        },
    ]
    recurring = [
        {"code": "90935", "typical_frequency": "3X_WEEK", "note": "Hemodialysis"},
        {"code": "96413", "typical_frequency": "EVERY_21D", "note": "Chemo cycle"},
        {"code": "97110", "typical_frequency": "3X_WEEK", "note": "Therapy plan of care"},
        {"code": "97140", "typical_frequency": "3X_WEEK", "note": "Therapy plan of care"},
        {"code": "J1100", "typical_frequency": "WEEKLY", "note": "Injection series"},
    ]
    return [
        {"dataset": "NCCI_PTP_PRACTITIONER", "version": "DEV-2026Q3", "effective_from": "2026-07-01", "rows": ptp},
        {"dataset": "NCCI_PTP_HOSPITAL", "version": "DEV-2026Q3", "effective_from": "2026-07-01", "rows": ptp},
        {"dataset": "MUE_PRACTITIONER", "version": "DEV-2026Q3", "effective_from": "2026-07-01", "rows": mue},
        {"dataset": "MUE_HOSPITAL", "version": "DEV-2026Q3", "effective_from": "2026-07-01", "rows": mue},
        {"dataset": "PFS_GLOBAL", "version": "DEV-2026", "effective_from": "2026-01-01", "rows": glob},
        {"dataset": "HCPCS", "version": "DEV-2026", "rows": hcpcs},
        {"dataset": "FREQUENCY_LIMITS", "version": "DEV-1", "rows": freq},
        {"dataset": "RECURRING_SERIES", "version": "DEV-1", "rows": recurring},
    ]


def dev_private_key() -> str:
    p = Path(__file__).resolve().parents[3] / "refdata" / "keys" / "dev-signing.private"
    return p.read_text().strip()


def install_dev_sample(eng: Engine) -> dict[str, Any]:
    """Sign the dev sample with a throwaway key and import it (tests / demo only)."""
    k = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization as s

    priv = k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()
    pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex()
    blob = build_bundle(dev_sample_datasets(), priv, "DEV-SAMPLE")
    return import_bundle(eng, blob, trusted=[pub])
