"""Model package import (Settings -> AI -> Import model package).

A package is verismo-models-<tier>.zip containing manifest.json and one or more .gguf files.
Every file is hash-verified against the manifest before it is installed; only Apache-2.0 / MIT
licensed models are accepted. Files are streamed, never fully loaded into memory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from verismo_engine.context import Engine
from verismo_engine.security import audit

PERMITTED = {"Apache-2.0", "MIT"}


def import_model_package(eng: Engine, path: Path, user_id: int | None) -> dict[str, Any]:
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("choose a verismo-models-*.zip package")
    dest = eng.config.models_dir
    dest.mkdir(parents=True, exist_ok=True)
    installed = []
    with zipfile.ZipFile(path) as z:
        try:
            manifest = json.loads(z.read("manifest.json"))
        except KeyError as e:
            raise ValueError("package has no manifest.json") from e
        for m in manifest.get("models", []):
            name = Path(m["file"]).name
            if name != m["file"] or not name.endswith(".gguf"):
                raise ValueError(f"invalid model file name {m['file']!r}")
            if m.get("license") not in PERMITTED:
                raise ValueError(f"{name}: licence {m.get('license')!r} is not permitted")
            tmp = dest / (name + ".part")
            h = hashlib.sha256()
            with z.open(m["file"]) as src, tmp.open("wb") as out:
                while chunk := src.read(8 * 2**20):
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != m["sha256"]:
                tmp.unlink()
                raise ValueError(f"{name}: sha256 mismatch; package is corrupt or tampered with")
            tmp.replace(dest / name)
            installed.append({"file": name, "sha256": m["sha256"], "license": m["license"], "tier": m.get("tier")})
    mpath = dest / "manifest.json"
    current = json.loads(mpath.read_text()) if mpath.exists() else {"models": []}
    by_file = {m["file"]: m for m in current.get("models", [])}
    for m in manifest.get("models", []):
        by_file[m["file"]] = m
    mpath.write_text(json.dumps({"models": list(by_file.values())}, indent=1))
    audit.record(
        eng.db,
        "MODEL_IMPORT",
        user_id=user_id,
        entity_type="model_package",
        entity_id=path.name,
        after={"installed": installed},
    )
    return {"installed": installed, "models_dir": str(dest)}


def copy_embedding_model(src: Path, eng: Engine) -> None:  # pragma: no cover - build-time helper
    shutil.copytree(src, eng.config.models_dir / "embeddings", dirs_exist_ok=True)
