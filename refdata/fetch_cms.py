"""Download CMS source files on the BUILD machine (the only networked step in the product).

CMS relocates quarterly files, so URLs are supplied explicitly (from the CMS NCCI, MUE and PFS
pages) in a small JSON manifest rather than hard-coded:

    {"ptp_practitioner": "https://www.cms.gov/files/zip/...zip", "mue_practitioner": "...", ...}

    uv run python refdata/fetch_cms.py sources-2026Q3.json --out refdata/downloads/2026Q3

Each file's SHA-256 is written next to it (downloads.sha256) for the release record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    urls = json.loads(a.sources.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, url in urls.items():
        if not url.startswith("https://www.cms.gov/"):
            raise SystemExit(f"{name}: only https://www.cms.gov/ URLs are accepted")
        dest = a.out / f"{name}{Path(url).suffix or '.bin'}"
        with urllib.request.urlopen(url, timeout=120) as r, dest.open("wb") as f:  # noqa: S310 - fixed https host
            f.write(r.read())
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        lines.append(f"{digest}  {dest.name}  {url}")
        print(f"{name}: {dest} {digest[:12]}")
    (a.out / "downloads.sha256").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
