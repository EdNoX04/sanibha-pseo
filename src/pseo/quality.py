"""Stage 5 - automated QA gates. This is what replaces "someone reads every page".

Every page passes through the same gates. A failure is not a crash: its messages are fed back
to the writer as revision instructions (the agent loop). Only a page that still fails after
`max_revisions` is quarantined, and quarantine is reported and alerted, never published.
"""
from __future__ import annotations

import re

import numpy as np

from .keywords import content_tokens
from .models import KeywordRecord, PageBrief, PageContent, QAResult
from .similarity import jaccard, sentences, shingles, words

BANNED = [
    "in today's digital", "look no further", "game-changer", "game changer", "seamless",
    "unlock the", "effortless", "whether you're a student", "in this article we will",
    "revolutionize", "cutting-edge", "dive into", "delve", "elevate your",
]
OVERCLAIMS = [
    r"\b100\s?% (?:accura|secure|safe|private|guarantee|success)",
    r"(?<!no )(?<!not )(?<!can't )(?<!cannot )\bguarantee[sd]?\b",
    r"\bperfect(?:ly)? accura",
    r"\b(?:will )?always (?:be )?accepted\b",
]
# words that carry no topic in a question; ignored when comparing FAQ questions across pages
FAQ_NOISE = {"iphone", "ipad", "app", "free", "what", "when", "where", "why", "which", "who", "do",
             "does", "did", "you", "your", "people", "are", "use", "using", "need", "get", "should", "will"}
GENERIC_HEADINGS = {"how it works", "features", "faq", "tips", "alternatives", "steps", "key features"}


def page_text(c: PageContent) -> str:
    """All human-visible copy, in reading order. Used for length and similarity checks."""
    parts = [c.h1, c.quick_answer, *c.intro, c.steps_heading]
    parts += [f"{s.title}. {s.detail}" for s in c.steps]
    parts += [c.alternatives_heading]
    parts += [f"{a.method}. {a.how} {a.best_for} {a.limitation}" for a in c.alternatives]
    parts += [c.features_heading] + [f.benefit for f in c.feature_points]
    parts += [c.tips_heading, *c.tips]
    parts += [f"{f.question} {f.answer}" for f in c.faqs]
    parts += [c.cta.heading, c.cta.text]
    return "\n".join(parts)


def field_texts(c: PageContent) -> dict[str, str]:
    """Visible text per top-level field, used to attribute an error to the field it lives in."""
    return {
        "title": c.title, "meta_description": c.meta_description, "h1": c.h1,
        "quick_answer": c.quick_answer, "intro": " ".join(c.intro),
        "steps_heading": c.steps_heading, "steps": " ".join(f"{s.title}. {s.detail}" for s in c.steps),
        "alternatives_heading": c.alternatives_heading,
        "alternatives": " ".join(f"{a.method}. {a.how} {a.best_for} {a.limitation}" for a in c.alternatives),
        "features_heading": c.features_heading, "feature_points": " ".join(f.benefit for f in c.feature_points),
        "tips_heading": c.tips_heading, "tips": " ".join(c.tips),
        "faqs": " ".join(f"{f.question} {f.answer}" for f in c.faqs),
        "cta": f"{c.cta.heading} {c.cta.text}",
    }


class _Errors:
    """Collects errors and the fields each one lives in (None = the whole page)."""

    def __init__(self, fields: dict[str, str]):
        self.items: list[tuple[str, list[str] | None]] = []
        self._lower = {k: v.lower() for k, v in fields.items()}
        self._norm = {k: " ".join(words(v)) for k, v in fields.items()}

    def add(self, msg: str, fields: list[str] | None) -> None:
        self.items.append((msg, fields))

    def locate(self, phrase: str, normalised: bool = False) -> list[str] | None:
        hay = self._norm if normalised else self._lower
        hits = [k for k, v in hay.items() if phrase in v]
        return hits or None


def _count(name: str, items: list, lo: int, hi: int, errs: _Errors) -> None:
    if not lo <= len(items) <= hi:
        errs.add(f"{name}: has {len(items)} items, needs {lo}-{hi}", [name])


def check_page(c: PageContent, rec: KeywordRecord, brief: PageBrief, app: dict,
               index, embedding: np.ndarray | None, cfg,
               faq_embeddings: np.ndarray | None = None) -> QAResult:
    """index: a CorpusIndex of published pages (the page itself excluded by slug)."""
    q, d = cfg["quality"], cfg["dedupe"]
    warnings, overlaps = [], []
    text = page_text(c)
    n_words = len(words(text))
    lower = text.lower()
    features = {f["id"]: f for f in app["features"]}
    errs = _Errors(field_texts(c))

    # ---- structure & SEO basics --------------------------------------------------------
    if len(c.title) > q["title_max_chars"]:
        errs.add(f"title is {len(c.title)} chars, max {q['title_max_chars']}: '{c.title}'", ["title"])
    if not q["meta_min_chars"] <= len(c.meta_description) <= q["meta_max_chars"]:
        errs.add(f"meta_description is {len(c.meta_description)} chars, needs "
                 f"{q['meta_min_chars']}-{q['meta_max_chars']}", ["meta_description"])
    if not q["min_words"] <= n_words <= q["max_words"]:
        errs.add(f"page has {n_words} words, needs {q['min_words']}-{q['max_words']}", None)
    kw_tokens = set(rec.tokens)
    head_tokens = set(content_tokens(c.title + " " + c.h1))
    # allow simple plural/singular variation (pdf/pdfs, receipt/receipts)
    covered = {t for t in kw_tokens if t in head_tokens or t + "s" in head_tokens or t.rstrip("s") in head_tokens}
    coverage = len(covered) / max(len(kw_tokens), 1)
    if coverage < q["min_keyword_coverage"]:
        errs.add(f"title+H1 cover only {coverage:.0%} of the keyword terms (missing: {sorted(kw_tokens - covered)})",
                 ["title", "h1"])
    _count("steps", c.steps, 3, 8, errs)
    _count("alternatives", c.alternatives, 2, 4, errs)
    _count("feature_points", c.feature_points, 3, 5, errs)
    _count("tips", c.tips, 3, 5, errs)
    _count("faqs", c.faqs, 4, 6, errs)
    for name in ("steps_heading", "alternatives_heading", "features_heading", "tips_heading"):
        h = getattr(c, name)
        if h.strip().lower() in GENERIC_HEADINGS:
            errs.add(f"heading '{h}' is generic; make it specific to this keyword", [name])

    # ---- grounding: features must exist and pro features must not be called free ----------
    for fp in c.feature_points:
        f = features.get(fp.feature_id)
        if not f:
            errs.add(f"feature_points uses unknown feature id '{fp.feature_id}'", ["feature_points"])
        elif f["tier"] == "pro" and re.search(r"\bfree\b", fp.benefit.lower()) and "not free" not in fp.benefit.lower():
            errs.add(f"'{f['name']}' is a Pro feature but its benefit text says free", ["feature_points"])
    for f in features.values():
        if f["tier"] != "pro":
            continue
        for s in re.split(r"(?<=[.!?])\s+", text):
            sl = s.lower()
            if f["name"].lower() in sl and re.search(r"\bfree\b", sl) and not re.search(r"\bpro\b|not free|paid|purchase", sl):
                warnings.append(f"check pricing claim: '{s.strip()[:120]}'")

    # ---- style --------------------------------------------------------------------------
    for b in BANNED:
        if b in lower:
            errs.add(f"banned filler phrase: '{b}'", errs.locate(b))
    for pat in OVERCLAIMS:
        m = re.search(pat, lower)
        if m:
            errs.add(f"over-claim '{m.group(0)}' - remove or qualify it", errs.locate(m.group(0)))

    # ---- duplicate content vs the published corpus --------------------------------------
    sh = shingles(text)
    sents = sentences(text)
    max_j, max_j_slug, max_cos, max_cos_slug = 0.0, None, 0.0, None
    title_tokens = set(content_tokens(c.title))
    candidates = index.candidates(sh, embedding, exclude=rec.slug)
    for other in candidates:
        j = jaccard(sh, other["shingles"])
        if j > max_j:
            max_j, max_j_slug = j, other["slug"]
        for s in sents & other["sentences"]:
            overlaps.append((other["slug"], s))
        ot = set(content_tokens(other["title"]))
        if title_tokens and jaccard(title_tokens, ot) >= 0.8:
            errs.add(f"title is near-identical to /{other['slug']}: '{other['title']}'", ["title"])
        if c.meta_description.strip().lower() == other["meta"].strip().lower():
            errs.add(f"meta description duplicates /{other['slug']}", ["meta_description"])
        # FAQ-level overlap: two pages answering the same question is cannibalisation in
        # miniature, even when the pages around them differ (caught on the OCR/image-to-text pair)
        for f in c.faqs:
            qt = set(content_tokens(f.question)) - FAQ_NOISE
            for oq in other.get("faqs", []):
                ot2 = set(content_tokens(oq)) - FAQ_NOISE
                if qt and ot2 and jaccard(qt, ot2) >= d["faq_max_overlap"]:
                    errs.add(f"FAQ '{f.question}' duplicates /{other['slug']}'s FAQ '{oq}'; "
                             "replace it with a question only this page's searcher would ask", ["faqs"])
        oe = other.get("faq_emb")
        if faq_embeddings is not None and len(faq_embeddings) and oe is not None and len(oe):
            sims = faq_embeddings @ oe.T
            for x, y in zip(*np.where(sims >= d["faq_max_cosine"])):
                errs.add(f"FAQ '{c.faqs[x].question}' asks the same thing as /{other['slug']}'s "
                         f"'{other['faqs'][y]}' (cosine {sims[x, y]:.3f}); link to that page instead "
                         "and ask a question specific to this page", ["faqs"])
    for slug, cos in index.cosine_to_all(embedding, exclude=rec.slug):
        if cos > max_cos:
            max_cos, max_cos_slug = cos, slug
    if max_j > d["page_max_jaccard"]:
        errs.add(f"too much text overlap with /{max_j_slug} (5-gram Jaccard {max_j:.3f} > {d['page_max_jaccard']})", None)
    if len(overlaps) > d["max_shared_sentences"]:
        where = set()
        for _, s in overlaps:
            loc = errs.locate(s, normalised=True)
            where = None if (loc is None or where is None) else where | set(loc)
        errs.add(f"{len(overlaps)} sentences appear verbatim on other pages; rewrite them: "
                 + " | ".join(f"[{o}] {s[:140]}" for o, s in overlaps[:6]), sorted(where) if where else None)
    if max_cos > d["page_max_cosine"]:
        o = index.get(max_cos_slug) or {"headings": []}
        errs.add(
            f"semantically too close to /{max_cos_slug} (cosine {max_cos:.3f} > {d['page_max_cosine']}). "
            f"That page already covers: {' / '.join(o['headings'])}. Do not re-explain what it explains; "
            f"mention it in one sentence and link. Rebuild the steps, tips and FAQs around this page's own "
            f"angle: {brief.unique_angle}", None)

    errors = list(dict.fromkeys(m for m, _ in errs.items))
    page_level = any(f is None for _, f in errs.items)
    fix_fields = [] if page_level else sorted({x for _, f in errs.items for x in f})
    return QAResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        overlaps=[f"[{o}] {s}" for o, s in overlaps],
        fix_fields=fix_fields,
        page_level=page_level,
        metrics={
            "words": n_words,
            "keyword_coverage": round(coverage, 2),
            "max_jaccard": round(max_j, 4), "max_jaccard_with": max_j_slug,
            "max_cosine": round(max_cos, 4), "max_cosine_with": max_cos_slug,
            "shared_sentences": len(overlaps),
            "compared_with": len(candidates),
        },
    )
