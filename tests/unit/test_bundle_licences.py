"""Licence collection for the bundled Tesseract (tools/bundle_tesseract.py), Windows/MSYS2 path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bundle_tesseract as bt  # noqa: E402

P = bt.MSYS_PREFIX


def _msys(tmp_path: Path, licences: dict[str, str]) -> Path:
    root = tmp_path / "msys64"
    for name, text in licences.items():
        d = root / "ucrt64" / "share" / "licenses" / name
        d.mkdir(parents=True)
        (d / "LICENSE").write_text(text)
    (root / "ucrt64" / "bin").mkdir(parents=True)
    return root


def _owners(monkeypatch: pytest.MonkeyPatch, root: Path, pkgs: list[str]) -> list[Path]:
    monkeypatch.setenv("MSYS2_ROOT", str(root))
    monkeypatch.setattr(bt, "run", lambda *_a: "\n".join(pkgs))  # stands in for `pacman -Qqo ...`
    return [root / "ucrt64" / "bin" / f"f{i}.dll" for i in range(len(pkgs))]


def test_windows_licences_use_package_dirs_name_variants_and_repo_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _msys(tmp_path, {"tesseract-ocr": "Apache", "leptonica": "BSD", "openjpeg": "BSD-2"})
    pkgs = [f"{P}tesseract-ocr", f"{P}leptonica", f"{P}openjpeg2", f"{P}giflib"]
    out = tmp_path / "licenses"
    found = bt.licences_windows(_owners(monkeypatch, root, pkgs), out)
    assert found == ["giflib", "leptonica", "openjpeg2", "tesseract-ocr"]
    assert (out / "openjpeg2" / "LICENSE").read_text() == "BSD-2"  # share/licenses/openjpeg
    assert "MIT" in (out / "giflib" / "COPYING").read_text()  # from tools/third_party_licenses


def test_windows_licences_fail_when_a_bundled_package_has_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _msys(tmp_path, {"tesseract-ocr": "Apache"})
    sources = _owners(monkeypatch, root, [f"{P}tesseract-ocr", f"{P}some-new-lib"])
    with pytest.raises(SystemExit, match="some-new-lib"):
        bt.licences_windows(sources, tmp_path / "licenses")
