"""Tesseract 5 OCR over stdin/stdout: page images never touch the disk (no temp files)."""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class OCRUnavailable(RuntimeError):
    pass


@dataclass
class OcrWord:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    line_key: tuple[int, int, int]


def tesseract_binary() -> str:
    env = os.environ.get("VERISMO_TESSERACT")
    if env:
        return env
    if getattr(sys, "frozen", False):  # bundled next to the engine executable
        cand = Path(sys.executable).parent / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")
        if cand.exists():
            return str(cand)
    found = shutil.which("tesseract")
    if not found:
        raise OCRUnavailable("tesseract not found")
    return found


def ocr_png(png: bytes, *, timeout: float = 60.0, psm: int = 6) -> list[OcrWord]:
    exe = tesseract_binary()
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        env["TESSDATA_PREFIX"] = str(Path(exe).parent / "tessdata")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [exe, "stdin", "stdout", "-l", "eng", "--psm", str(psm), "tsv"],
        input=png,
        capture_output=True,
        timeout=timeout,
        env=env,
        creationflags=flags,
        check=False,
    )
    if proc.returncode != 0:
        raise OCRUnavailable(f"tesseract failed ({proc.returncode})")
    words: list[OcrWord] = []
    reader = csv.DictReader(io.StringIO(proc.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE)
    for r in reader:
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        try:
            words.append(
                OcrWord(
                    txt,
                    float(r["conf"]),
                    int(r["left"]),
                    int(r["top"]),
                    int(r["width"]),
                    int(r["height"]),
                    (int(r["block_num"]), int(r["par_num"]), int(r["line_num"])),
                )
            )
        except (KeyError, ValueError):
            continue
    return words


def words_to_lines(words: list[OcrWord]) -> list[tuple[str, float]]:
    """Group OCR words into text lines; returns (text, mean confidence 0..1)."""
    lines: dict[tuple[int, int, int], list[OcrWord]] = {}
    for w in words:
        lines.setdefault(w.line_key, []).append(w)
    out = []
    for key in sorted(lines, key=lambda k: (min(w.top for w in lines[k]), k)):
        ws = sorted(lines[key], key=lambda w: w.left)
        conf = sum(max(0.0, w.conf) for w in ws) / (100 * len(ws))
        out.append((" ".join(w.text for w in ws), conf))
    return out
