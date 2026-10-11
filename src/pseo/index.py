"""In-memory index of every published page, kept in step with the database.

Why it exists: the duplicate checks compare each draft with the whole published corpus. Rebuilding
that corpus from SQLite for every draft (decode JSON, re-split sentences, re-parse vectors) is
O(corpus) Python work per check and dominates at a few hundred pages. The index holds everything
pre-computed, updates incrementally when a page is saved, and answers with numpy:

* embeddings are one matrix, so "closest page by meaning" is one matrix-vector product;
* sentence sets, shingle sets and FAQ vectors are computed once per page, not once per check;
* above `exact_compare_below` pages, candidate generation switches to MinHash-LSH plus the
  nearest embedding neighbours, so each draft is checked exactly against tens of pages, not all.

It is thread-safe: several pages are generated in parallel and all read/write the same index.
"""
from __future__ import annotations

import threading

import numpy as np

from .models import PageContent
from .similarity import MinHashLSH, sentences


class CorpusIndex:
    def __init__(self, exact_below: int = 2000, neighbours: int = 25, lsh_perms: int = 128, lsh_bands: int = 64):
        self.exact_below = exact_below
        self.neighbours = neighbours
        self.entries: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._emb_slugs: list[str] = []
        self._emb_matrix: np.ndarray | None = None
        self._dirty = True
        self._lsh = MinHashLSH(num_perm=lsh_perms, bands=lsh_bands)
        self._lsh_ready = False

    # ------------------------------------------------------------------ build / update
    @classmethod
    def from_store(cls, store, cfg) -> "CorpusIndex":
        d = cfg["dedupe"]
        idx = cls(exact_below=d["exact_compare_below"], neighbours=d["candidate_neighbours"],
                  lsh_perms=d["minhash_perms"], lsh_bands=d["lsh_bands"])
        for e in store.corpus():
            idx.put(e["slug"], e)
        return idx

    def put(self, slug: str, e: dict) -> None:
        """e: a store.corpus()-style dict (body_text, shingles, embedding, faqs, faq_emb, title, meta, headings)."""
        entry = dict(e)
        entry["sentences"] = sentences(entry.get("body_text") or "")
        with self._lock:
            self.entries[slug] = entry
            self._dirty = True
            if self._lsh_ready:
                self._lsh.add(slug, self._lsh.signature(entry["shingles"]))

    def put_page(self, slug: str, content: PageContent, body_text: str, shingles: set, embedding,
                 faq_emb) -> None:
        self.put(slug, {
            "slug": slug, "body_text": body_text, "shingles": set(shingles), "embedding": embedding,
            "faqs": [f.question for f in content.faqs], "faq_emb": faq_emb, "title": content.title,
            "meta": content.meta_description,
            "headings": [content.h1, content.steps_heading, content.alternatives_heading, content.tips_heading],
        })

    def remove(self, slug: str) -> None:
        with self._lock:
            if self.entries.pop(slug, None) is not None:
                self._dirty = True
                if self._lsh_ready:
                    self._lsh.remove(slug)

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, slug: str) -> dict | None:
        return self.entries.get(slug)

    # ------------------------------------------------------------------ queries
    def _matrix(self):
        with self._lock:
            if self._dirty:
                self._emb_slugs = [s for s, e in self.entries.items() if e.get("embedding") is not None]
                self._emb_matrix = (np.stack([self.entries[s]["embedding"] for s in self._emb_slugs])
                                    if self._emb_slugs else None)
                self._dirty = False
            return self._emb_slugs, self._emb_matrix

    def cosine_to_all(self, embedding, exclude: str | None = None) -> list[tuple[str, float]]:
        slugs, M = self._matrix()
        if M is None or embedding is None:
            return []
        sims = M @ embedding
        return [(s, float(v)) for s, v in zip(slugs, sims) if s != exclude]

    def candidates(self, shingles: set, embedding, exclude: str | None = None) -> list[dict]:
        """Pages a draft must be compared with exactly. All pages while the corpus is small;
        LSH bucket-mates + nearest embedding neighbours once it is large."""
        with self._lock:
            n = len(self.entries)
            if n < self.exact_below:
                return [e for s, e in self.entries.items() if s != exclude]
            if not self._lsh_ready:
                for s, e in self.entries.items():
                    self._lsh.add(s, self._lsh.signature(e["shingles"]))
                self._lsh_ready = True
            cand = set(self._lsh.query(self._lsh.signature(shingles)))
        sims = sorted(self.cosine_to_all(embedding, exclude), key=lambda x: -x[1])[: self.neighbours]
        cand |= {s for s, _ in sims}
        cand.discard(exclude)
        return [self.entries[s] for s in cand if s in self.entries]

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.entries.values())

