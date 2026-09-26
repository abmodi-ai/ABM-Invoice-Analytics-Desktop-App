"""Description embeddings for CLN-008.

Production: bge-small-en-v1.5 through ONNX Runtime (fastembed), loaded from the local models
directory only (never downloaded at runtime). If the model is not installed, a deterministic
hashing embedder is used and every CLN-008 flag records which embedder produced it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger("invoice_analytics.embeddings")
DIM = 384
BGE_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    embedder_id: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Word + character-trigram feature hashing into 384 dims, L2-normalized."""

    embedder_id = "hashing-v1"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        toks = text.split()
        feats = [f"w:{t}" for t in toks]
        for t in toks:
            p = f"#{t}#"
            feats.extend(f"c:{p[i:i + 3]}" for i in range(max(1, len(p) - 2)))
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            idx = h % DIM
            sign = 1.0 if (h >> 32) & 1 else -1.0
            v[idx] += sign * (2.0 if f.startswith("w:") else 1.0)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        return np.vstack([self._vec(t) for t in texts])


class BgeEmbedder:  # pragma: no cover - requires installed model files
    embedder_id = "bge-small-en-v1.5-onnx"

    def __init__(self, cache_dir: Path) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(BGE_MODEL, cache_dir=str(cache_dir), local_files_only=True)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        return np.vstack(list(self._model.embed(texts))).astype(np.float32)


_lock = threading.Lock()
_cached: Embedder | None = None


def get_embedder(models_dir: Path | None = None) -> Embedder:
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        mode = os.environ.get("IA_EMBEDDER", "auto")
        emb: Embedder = HashingEmbedder()
        if mode in ("auto", "bge") and models_dir is not None:
            cache = models_dir / "embeddings"
            if cache.exists():
                try:
                    emb = BgeEmbedder(cache)
                except Exception as e:  # noqa: BLE001
                    if mode == "bge":
                        raise
                    log.warning("bge embedder unavailable, using hashing fallback: %s", type(e).__name__)
        _cached = emb
        return emb


def reset_embedder() -> None:
    global _cached
    with _lock:
        _cached = None


def to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def from_blob(b: bytes | None) -> np.ndarray | None:
    if not b:
        return None
    return np.frombuffer(b, dtype=np.float32)
