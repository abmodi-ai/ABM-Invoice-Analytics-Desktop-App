"""License gate for RUNTIME Python dependencies (CI fails on any violation).

Only the packages that ship inside the engine are checked: the transitive closure of the
[project] dependencies. Dev/build tools (pytest, hypothesis MPL-2.0, PyInstaller GPL+bootloader
exception, mypy, ...) never ship and are out of scope, as documented in docs/adr/0002-license-policy.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata

PERMISSIVE = re.compile(
    r"\b(MIT|BSD|Apache|ISC|PSF|Python Software Foundation|MPL-2\.0 OR|Unlicense|Zlib|HPND|CC0|0BSD|"
    r"Historical Permission|The Unlicense|Public Domain|UPL)\b",
    re.I,
)
COPYLEFT = re.compile(r"\b(AGPL|GPL|LGPL|SSPL|EUPL|CDDL|Artistic)\b", re.I)

# Reviewed exceptions: package -> reason. Keep this list short and justified.
ALLOW = {
    "certifi": "MPL-2.0, unmodified CA bundle data file (weak copyleft, file-level; permitted by policy)",
    "pypdfium2": "BSD-3-Clause / Apache-2.0 (PDFium); metadata string lists several licenses",
    "pypdfium2_raw": "PDFium binary, BSD-3-Clause / Apache-2.0",
    "tqdm": "MPL-2.0 AND MIT (weak copyleft, file-level; unmodified)",
    "fqdn": "MPL-2.0 (file-level weak copyleft, unmodified)",
}


def runtime_closure() -> set[str]:
    root = metadata.distribution("verismo-engine")
    todo = [r for r in (root.requires or []) if "extra ==" not in r]
    seen: set[str] = set()
    while todo:
        req = todo.pop()
        name = re.split(r"[ ;<>=!~\[(]", req, maxsplit=1)[0].strip().lower().replace("_", "-")
        if ";" in req and "sys_platform" in req and ("win32" in req) != (sys.platform == "win32"):
            continue
        if not name or name in seen:
            continue
        try:
            d = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen.add(name)
        todo.extend(r for r in (d.requires or []) if "extra ==" not in r)
    return seen


def main() -> int:
    closure = runtime_closure()
    out = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-system"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = {r["Name"].lower().replace("_", "-"): r for r in json.loads(out)}
    bad = []
    for name in sorted(closure):
        r = rows.get(name)
        lic = (r or {}).get("License", "UNKNOWN")
        norm = name.replace("-", "_")
        if name in ALLOW or norm in ALLOW:
            continue
        if COPYLEFT.search(lic) and not PERMISSIVE.search(lic.split(";")[0]) or not PERMISSIVE.search(lic):
            bad.append((name, lic))
    print(f"checked {len(closure)} runtime packages")
    for n, lic in bad:
        print(f"  VIOLATION {n}: {lic}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
