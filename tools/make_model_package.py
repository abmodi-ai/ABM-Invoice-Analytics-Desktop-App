"""Build verismo-models-<tier>.zip from GGUF files (build machine).

    uv run python tools/make_model_package.py --tier LITE --license Apache-2.0 \
        --source https://huggingface.co/<repo> qwen3-4b-instruct-q4_k_m.gguf --out build/models

Only Apache-2.0 / MIT models are accepted (the engine enforces the same rule on import).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--tier", required=True, choices=["LITE", "STANDARD", "PLUS"])
    ap.add_argument("--license", required=True, choices=["Apache-2.0", "MIT"])
    ap.add_argument("--source", required=True, help="where the weights were obtained (recorded in the manifest)")
    ap.add_argument("--out", type=Path, default=Path("build/models"))
    a = ap.parse_args()
    models = []
    for f in a.files:
        h = hashlib.sha256()
        with f.open("rb") as fh:
            while chunk := fh.read(16 * 2**20):
                h.update(chunk)
        models.append(
            {
                "file": f.name,
                "sha256": h.hexdigest(),
                "license": a.license,
                "tier": a.tier,
                "source": a.source,
                "bytes": f.stat().st_size,
            }
        )
    a.out.mkdir(parents=True, exist_ok=True)
    out = a.out / f"verismo-models-{a.tier.lower()}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        z.writestr("manifest.json", json.dumps({"models": models}, indent=1))
        for f in a.files:
            z.write(f, f.name)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
