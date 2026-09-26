"""Fellegi-Sunter probabilistic record linkage with EM-estimated m/u parameters.

Replaces Splink (its igraph dependency is GPL; see docs/adr/0005-linkage-without-splink.md).
Same model: each comparison has discrete agreement levels; u = P(level | non-match) is estimated
from random record pairs, m = P(level | match) and the prior lambda by expectation-maximisation
over blocked candidate pairs. Match weight = log2(lambda/(1-lambda)) + sum log2(m/u). Every
prediction carries its per-field levels and weights, so it is explainable to a reviewer.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

Record = dict[str, Any]
# returns a level index, or None when the comparison is not informative (a value is missing)
LevelFn = Callable[[Record, Record], int | None]


@dataclass
class Comparison:
    name: str
    levels: list[str]  # level 0 is the strongest agreement; the last level is "else"
    fn: LevelFn
    m_init: list[float] | None = None
    # Upper bound on P(complete disagreement | match). Without it EM can "discover" a cluster of
    # look-alikes (same surname + DOB, different first name) and treat it as the match class.
    m_else_max: float | None = None


@dataclass
class Model:
    comparisons: list[Comparison]
    blocking: list[Callable[[Record], Any]]
    m: list[np.ndarray] = field(default_factory=list)
    u: list[np.ndarray] = field(default_factory=list)
    prior: float = 1e-4
    trained: bool = False

    # ------------------------------------------------------------ pair generation
    def candidate_pairs(self, recs: Sequence[Record], max_block: int = 500) -> list[tuple[int, int]]:
        seen: set[tuple[int, int]] = set()
        for key_fn in self.blocking:
            blocks: dict[Any, list[int]] = defaultdict(list)
            for i, r in enumerate(recs):
                k = key_fn(r)
                if k is not None and k != "" and k != ():
                    if isinstance(k, list):
                        for kk in k:
                            blocks[kk].append(i)
                    else:
                        blocks[k].append(i)
            for idx in blocks.values():
                if len(idx) < 2 or len(idx) > max_block:
                    continue
                for x in range(len(idx)):
                    for y in range(x + 1, len(idx)):
                        a, b = idx[x], idx[y]
                        seen.add((a, b) if a < b else (b, a))
        return sorted(seen)

    def vectors(self, recs: Sequence[Record], pairs: Sequence[tuple[int, int]]) -> np.ndarray:
        """Comparison vectors: shape (pairs, comparisons); -1 = not informative."""
        out = np.full((len(pairs), len(self.comparisons)), -1, dtype=np.int8)
        for p, (i, j) in enumerate(pairs):
            a, b = recs[i], recs[j]
            for c, comp in enumerate(self.comparisons):
                lv = comp.fn(a, b)
                if lv is not None:
                    out[p, c] = lv
        return out

    # ------------------------------------------------------------ estimation
    def estimate_u(self, recs: Sequence[Record], samples: int = 100_000, seed: int = 7) -> None:
        rng = random.Random(seed)
        n = len(recs)
        if n < 2:
            self.u = [np.full(len(c.levels), 1 / len(c.levels)) for c in self.comparisons]
            return
        pairs = []
        for _ in range(min(samples, n * (n - 1) // 2)):
            i, j = rng.randrange(n), rng.randrange(n)
            if i != j:
                pairs.append((min(i, j), max(i, j)))
        v = self.vectors(recs, pairs)
        self.u = []
        for c, comp in enumerate(self.comparisons):
            col = v[:, c]
            col = col[col >= 0]
            counts = np.bincount(col, minlength=len(comp.levels)).astype(float) + 0.5  # smoothing
            self.u.append(counts / counts.sum())

    def estimate_em(self, v: np.ndarray, iterations: int = 25, tol: float = 1e-6) -> None:
        k = len(self.comparisons)
        if not self.m:
            self.m = []
            for comp in self.comparisons:
                if comp.m_init:
                    arr = np.array(comp.m_init, dtype=float)
                else:  # most mass on the strongest agreement level
                    arr = np.array([0.9] + [0.1 / max(1, len(comp.levels) - 1)] * (len(comp.levels) - 1))
                self.m.append(arr / arr.sum())
        lam = max(self.prior, 1e-3)
        for _ in range(iterations):
            logit = np.full(len(v), math.log(lam / (1 - lam)))
            for c in range(k):
                col = v[:, c]
                ok = col >= 0
                logit[ok] += np.log(self.m[c][col[ok]]) - np.log(self.u[c][col[ok]])
            post = 1 / (1 + np.exp(-np.clip(logit, -50, 50)))
            new_lam = float(post.mean())
            delta = abs(new_lam - lam)
            lam = min(max(new_lam, 1e-6), 0.999)
            for c, comp in enumerate(self.comparisons):
                col = v[:, c]
                ok = col >= 0
                w = np.bincount(col[ok], weights=post[ok], minlength=len(comp.levels)) + 1e-3
                m = w / w.sum()
                if comp.m_else_max is not None and m[-1] > comp.m_else_max:
                    rest = m[:-1].sum()
                    m[:-1] = m[:-1] / rest * (1 - comp.m_else_max)
                    m[-1] = comp.m_else_max
                self.m[c] = m
            if delta < tol:
                break
        self.prior = lam
        self.trained = True

    # ------------------------------------------------------------ prediction
    def predict(
        self,
        recs: Sequence[Record],
        pairs: Sequence[tuple[int, int]],
        v: np.ndarray,
        threshold: float = 0.0,
        lam: float | None = None,
    ) -> list[tuple[int, int, float, dict[str, Any]]]:
        prior = self.prior if lam is None else lam
        base = math.log2(prior / (1 - prior))
        out = []
        for p, (i, j) in enumerate(pairs):
            weight = base
            parts: dict[str, Any] = {}
            for c, comp in enumerate(self.comparisons):
                lv = int(v[p, c])
                if lv < 0:
                    parts[comp.name] = {"level": "null", "weight": 0.0}
                    continue
                w = math.log2(self.m[c][lv] / self.u[c][lv])
                weight += w
                parts[comp.name] = {"level": comp.levels[lv], "weight": round(w, 2)}
            prob = 1 / (1 + 2 ** (-weight))
            if prob >= threshold:
                out.append((i, j, prob, {"match_weight": round(weight, 2), "fields": parts}))
        return out

    def parameters(self) -> dict[str, Any]:
        return {
            "prior": self.prior,
            "comparisons": {
                c.name: {
                    lv: {"m": round(float(self.m[k][n]), 5), "u": round(float(self.u[k][n]), 6)}
                    for n, lv in enumerate(c.levels)
                }
                for k, c in enumerate(self.comparisons)
            },
        }
