"""Stage 2 - keyword clustering: duplicate prevention BEFORE any content is written.

Most duplicate content in programmatic SEO is decided at the keyword list, not at the writing
step: if two keywords mean the same thing, two pages will compete for one query (keyword
cannibalisation) no matter how differently they are worded. So:

* near-identical keywords (cosine >= keyword_merge_cosine) are MERGED: one URL, the other
  keyword becomes a secondary keyword of that page.
* close-but-distinct keywords (cosine >= keyword_sibling_cosine, or the nearest neighbours)
  become SIBLINGS: they are planned in the same LLM call, and each brief must state which
  sub-topics it owns and which it leaves (and links) to its sibling.
"""
from __future__ import annotations

import numpy as np

from .keywords import content_tokens
from .models import KeywordRecord
from .similarity import cosine_matrix


def _lexical(a: KeywordRecord, b: KeywordRecord) -> float:
    ta = set(a.tokens) - {"iphone", "free", "app"}
    tb = set(b.tokens) - {"iphone", "free", "app"}
    return len(ta & tb) / max(len(ta | tb), 1)


def cluster_keywords(records: list[KeywordRecord], emb: np.ndarray, *, merge_cos: float,
                     sibling_cos: float, existing: list[dict] | None = None,
                     neighbours: int = 6) -> dict:
    """Mutates records (siblings, merged_keywords, cluster_id). Returns a report dict.

    existing: already-published pages [{slug, keyword, embedding}] so new keywords are checked
    against the live corpus too, not only against this batch.
    """
    n = len(records)
    sims = cosine_matrix(emb, emb)
    np.fill_diagonal(sims, -1)
    merged_into: dict[int, int] = {}
    report = {"merged": [], "sibling_pairs": [], "existing_conflicts": []}

    # 1) merges within the batch (keep the first occurrence as the canonical page)
    for i in range(n):
        if i in merged_into:
            continue
        for j in range(i + 1, n):
            if j in merged_into or records[i].app_id != records[j].app_id:
                continue
            if sims[i, j] >= merge_cos:
                merged_into[j] = i
                records[i].merged_keywords.append(records[j].keyword)
                report["merged"].append((records[j].keyword, records[i].slug, round(float(sims[i, j]), 3)))

    # 2) merges against the published corpus
    if existing:
        ex_emb = np.stack([e["embedding"] for e in existing])
        ex_sims = cosine_matrix(emb, ex_emb)
        for i in range(n):
            if i in merged_into:
                continue
            j = int(np.argmax(ex_sims[i]))
            if ex_sims[i, j] >= merge_cos and existing[j]["slug"] != records[i].slug:
                merged_into[i] = -1
                report["existing_conflicts"].append(
                    (records[i].keyword, existing[j]["slug"], round(float(ex_sims[i, j]), 3)))

    live = [i for i in range(n) if i not in merged_into]

    # 3) siblings: above threshold, or lexically overlapping; always at least the nearest one
    parent = {i: i for i in live}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in live:
        order = [j for j in np.argsort(-sims[i]) if j in parent and j != i]
        sib = [j for j in order if sims[i, j] >= sibling_cos or _lexical(records[i], records[j]) >= 0.34]
        if not sib and order:
            sib = order[:1]
        records[i].siblings = [records[j].slug for j in sib[:3]]
        for j in sib[:3]:
            parent[find(i)] = find(j)
            if i < j:
                report["sibling_pairs"].append((records[i].slug, records[j].slug, round(float(sims[i, j]), 3)))

    roots = {}
    for i in live:
        records[i].cluster_id = roots.setdefault(find(i), len(roots))

    # 4) neighbour candidates for internal linking (batch + published corpus)
    pool_slugs = [records[i].slug for i in live]
    pool_vecs = [emb[i] for i in live]
    for e in existing or []:
        if e["slug"] not in pool_slugs:
            pool_slugs.append(e["slug"])
            pool_vecs.append(e["embedding"])
    nb = cosine_matrix(emb[live], np.stack(pool_vecs)) if pool_vecs else np.zeros((0, 0))
    neighbour_slugs = {}
    for row, i in enumerate(live):
        order = [k for k in np.argsort(-nb[row]) if pool_slugs[k] != records[i].slug]
        neighbour_slugs[records[i].slug] = [pool_slugs[k] for k in order[:neighbours]]

    return {
        "live": [records[i] for i in live],
        "merged_into": merged_into,
        "neighbour_slugs": neighbour_slugs,
        "report": report,
        "similarity": sims,
    }


def plan_batches(records: list[KeywordRecord], size: int) -> list[list[KeywordRecord]]:
    """Group by app, keep sibling clusters together, cap batch size so prompts stay small."""
    batches: list[list[KeywordRecord]] = []
    by_app: dict[str, list[KeywordRecord]] = {}
    for r in records:
        by_app.setdefault(r.app_id, []).append(r)
    for recs in by_app.values():
        recs = sorted(recs, key=lambda r: (r.cluster_id, r.slug))
        cur: list[KeywordRecord] = []
        for cid in dict.fromkeys(r.cluster_id for r in recs):
            group = [r for r in recs if r.cluster_id == cid]
            if cur and len(cur) + len(group) > size:
                batches.append(cur)
                cur = []
            while len(group) > size:          # a single huge cluster gets split
                batches.append(group[:size])
                group = group[size:]
            cur.extend(group)
        if cur:
            batches.append(cur)
    return batches


__all__ = ["cluster_keywords", "plan_batches", "content_tokens"]
