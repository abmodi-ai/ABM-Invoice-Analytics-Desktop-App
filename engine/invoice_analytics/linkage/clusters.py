"""Union-find used to turn accepted pairwise links into cluster ids."""

from __future__ import annotations

from collections import defaultdict


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self._members: dict[int, set[int]] | None = None

    def add(self, x: int) -> None:
        self.parent.setdefault(x, x)
        self._members = None

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb
            self._members = None

    def members(self, root: int) -> set[int]:
        if self._members is None:
            m: dict[int, set[int]] = defaultdict(set)
            for x in self.parent:
                m[self.find(x)].add(x)
            self._members = dict(m)
        return self._members.get(root, {root})
