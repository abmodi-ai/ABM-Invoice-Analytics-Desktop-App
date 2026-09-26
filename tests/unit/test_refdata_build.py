"""CMS source converters -> signed bundle -> import -> effective-dated lookups."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from invoice_analytics.clinical.refdata import (
    RefdataError,
    build_bundle,
    import_bundle,
    lookup_mue,
    lookup_ptp,
    verify_bundle,
)
from invoice_analytics.context import Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "refdata"))
import build_bundle as bb  # noqa: E402

PTP_TXT = (
    "CPT codes and descriptions only are copyright AMA.\n\n"
    "Column 1\tColumn 2\t*=in existence prior to 1996\tEffective Date\tDeletion Date *=no data\t"
    "Modifier 0=not allowed 1=allowed 9=not applicable\tPTP Edit Rationale\n"
    "20610\t20604\t\t20200101\t*\t0\tMutually exclusive\n"
    "97140\t97530\t\t20200101\t*\t1\tStandards of medical practice\n"
    "97110\t97750\t\t20150101\t20251231\t1\tDeleted edit\n"
)
MUE_CSV = (
    "Medically Unlikely Edits, Practitioner Services, effective July 1, 2026\n"
    "HCPCS/CPT Code,Practitioner Services MUE Values,MUE Adjudication Indicator,MUE Rationale\n"
    "97110,6,3 Date of Service Edit: Clinical,Clinical: Data\n"
    "20610,2,2 Date of Service Edit: Policy,Anatomic\n"
    "J1100,20,1 Line Edit,Prescribing info\n"
)
PFS_CSV = (
    "RVU file for 2026\n"
    "HCPCS,MOD,DESCRIPTION,CODE STATUS,GLOB DAYS\n"
    "29881,,,A,090\n29881,26,,A,090\n99213,,,A,XXX\n10060,,,A,010\n"
)


def _zip(name: str, text: str, tmp: Path) -> Path:
    p = tmp / f"{name}.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(f"{name}.txt", text)
    return p


def test_converters_parse_cms_layouts(tmp_path: Path) -> None:
    ptp = bb.parse_ptp(_zip("ptp", PTP_TXT, tmp_path))
    assert len(ptp) == 3 and ptp[2]["effective_to"] == "2025-12-31" and ptp[0]["modifier_indicator"] == "0"
    (tmp_path / "mue.csv").write_text(MUE_CSV)
    mue = bb.parse_mue(tmp_path / "mue.csv", "2026-07-01")
    assert {m["code"]: (m["mue_value"], m["mai"]) for m in mue} == {"97110": (6, 3), "20610": (2, 2), "J1100": (20, 1)}
    (tmp_path / "pfs.csv").write_text(PFS_CSV)
    g = bb.parse_pfs_global(tmp_path / "pfs.csv", 2026)
    assert {x["code"]: x["global_days"] for x in g} == {"29881": "090", "99213": "XXX", "10060": "010"}
    (tmp_path / "bad.csv").write_text("nothing,here\n1,2\n")
    with pytest.raises(bb.SourceError):
        bb.parse_mue(tmp_path / "bad.csv", "2026-07-01")


def test_cli_builds_importable_bundle(engine: Engine, tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    k = Ed25519PrivateKey.generate()
    (tmp_path / "k.private").write_text(k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex())
    pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex()
    (tmp_path / "mue.csv").write_text(MUE_CSV)
    out = tmp_path / "b.vref"
    rc = bb.main(
        [
            "--ptp-practitioner",
            str(_zip("ptp", PTP_TXT, tmp_path)),
            "--mue-practitioner",
            str(tmp_path / "mue.csv"),
            "--version",
            "2026Q3",
            "--effective-from",
            "2026-07-01",
            "--key",
            str(tmp_path / "k.private"),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    res = import_bundle(engine, out.read_bytes(), trusted=[pub])
    assert {d["dataset"] for d in res["datasets"]} == {"NCCI_PTP_PRACTITIONER", "MUE_PRACTITIONER"}
    assert lookup_mue(engine, "97110", "2026-08-01")["mue_value"] == 6
    assert lookup_ptp(engine, "97110", "97750", "2025-06-01") and not lookup_ptp(engine, "97110", "97750", "2026-06-01")


def test_tampered_bundle_rejected() -> None:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()
    pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex()
    blob = build_bundle(
        [
            {
                "dataset": "MUE_PRACTITIONER",
                "version": "X",
                "rows": [{"code": "97110", "mue_value": 6, "mai": 3, "effective_from": "2026-01-01"}],
            }
        ],
        priv,
        "X",
    )
    verify_bundle(blob, [pub])
    zin = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n.endswith(".csv"):
                data = data.replace(b",6,", b",99,")
            zout.writestr(n, data)
    with pytest.raises(RefdataError, match="hash mismatch"):
        verify_bundle(buf.getvalue(), [pub])
