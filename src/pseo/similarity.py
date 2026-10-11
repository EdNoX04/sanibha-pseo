"""Duplicate-content detection primitives.

Three independent signals, because each catches a different failure:

* Shingle Jaccard (lexical):  catches "same page, keyword swapped". Two pages built from one
  skeleton share most of their 5-word sequences even when nouns differ.
* Shared sentences (verbatim): catches boilerplate that leaks across pages (the same FAQ answer,
  the same CTA paragraph). Cheap to explain to a human: here are the repeated lines.
* Embedding cosine (semantic): catches paraphrased duplicates: different words, same content.

For scale, shingle comparison goes through MinHash + LSH so each new page is compared only to
candidate neighbours instead of every page in the corpus (O(n) instead of O(n^2) per run).
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

import numpy as np

_WORD = re.compile(r"[a-z0-9']+")
_SENT = re.compile(r"(?<=[.!?])\s+")
_MERSENNE = (1 << 61) - 1


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def shingles(text: str, k: int = 5) -> set[int]:
    w = words(text)
    return {int(hashlib.md5(" ".join(w[i:i + k]).encode()).hexdigest()[:15], 16)
            for i in range(max(len(w) - k + 1, 0))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def sentences(text: str, min_words: int = 8) -> set[str]:
    out = set()
    for s in _SENT.split(text):
        w = words(s)
        if len(w) >= min_words:
            out.add(" ".join(w))
    return out


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rows are assumed L2-normalised."""
    if a.size == 0 or b.size == 0:
        return np.zeros((len(a), len(b)))
    return a @ b.T


class MinHashLSH:
    """Minimal MinHash/LSH index (no external dependency).

    With 128 permutations in 64 bands of 2 rows, a pair with Jaccard 0.2 collides in at least
    one band ~93% of the time (1 - (1 - 0.2^2)^64). LSH is only the candidate generator: the
    candidates (plus the page's nearest embedding neighbours, which catch the rest) are then
    verified with exact Jaccard. Below a few thousand pages the pipeline skips LSH and compares
    every pair exactly, which is still only milliseconds per page.
    """

    def __init__(self, num_perm: int = 128, bands: int = 64, seed: int = 7):
        assert num_perm % bands == 0
        self.num_perm, self.bands, self.rows = num_perm, bands, num_perm // bands
        rng = np.random.default_rng(seed)
        self.a = rng.integers(1, 2**32, num_perm, dtype=np.uint64) | np.uint64(1)  # odd multipliers
        self.b = rng.integers(0, 2**32, num_perm, dtype=np.uint64)
        self.buckets: dict[tuple, set[str]] = defaultdict(set)
        self.keys: set[str] = set()

    def signature(self, sh: set[int]) -> np.ndarray:
        """Vectorised MinHash: h_i(x) = (a_i * x + b_i) mod 2^32 over 32-bit shingle hashes.
        a_i, b_i < 2^32 and x < 2^32 keep a_i * x inside uint64, so numpy does all permutations
        for all shingles in one broadcast instead of a Python double loop."""
        if not sh:
            return np.full(self.num_perm, 0xFFFFFFFF, dtype=np.uint64)
        x = np.fromiter(sh, dtype=np.uint64, count=len(sh)) & np.uint64(0xFFFFFFFF)
        h = (self.a[:, None] * x[None, :] + self.b[:, None]) & np.uint64(0xFFFFFFFF)
        return h.min(axis=1)

    def _band_keys(self, sig: np.ndarray):
        for band in range(self.bands):
            yield (band, sig[band * self.rows:(band + 1) * self.rows].tobytes())

    def add(self, key: str, sig: np.ndarray) -> None:
        self.remove(key)
        self.keys.add(key)
        for bk in self._band_keys(sig):
            self.buckets[bk].add(key)

    def remove(self, key: str) -> None:
        if key not in self.keys:
            return
        for members in self.buckets.values():
            members.discard(key)
        self.keys.discard(key)

    def query(self, sig: np.ndarray) -> set[str]:
        out: set[str] = set()
        for bk in self._band_keys(sig):
            out |= self.buckets.get(bk, set())
        return out
