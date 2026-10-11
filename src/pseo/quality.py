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


def _count(name: str, items: list, lo: int, hi: int, errors: list[str]) -> None:
    if not lo <= len(items) <= hi:
        errors.append(f"{name}: has {len(items)} items, needs {lo}-{hi}")


def check_page(c: PageContent, rec: KeywordRecord, brief: PageBrief, app: dict,
               corpus: list[dict], embedding: np.ndarray | None, cfg,
               faq_embeddings: np.ndarray | None = None) -> QAResult:
    q, d = cfg["quality"], cfg["dedupe"]
    errors, warnings, overlaps = [], [], []
    text = page_text(c)
    n_words = len(words(text))
    lower = text.lower()
    features = {f["id"]: f for f in app["features"]}

    # ---- structure & SEO basics --------------------------------------------------------
    if len(c.title) > q["title_max_chars"]:
        errors.append(f"title is {len(c.title)} chars, max {q['title_max_chars']}: '{c.title}'")
    if not q["meta_min_chars"] <= len(c.meta_description) <= q["meta_max_chars"]:
        errors.append(f"meta_description is {len(c.meta_description)} chars, needs {q['meta_min_chars']}-{q['meta_max_chars']}")
    if not q["min_words"] <= n_words <= q["max_words"]:
        errors.append(f"page has {n_words} words, needs {q['min_words']}-{q['max_words']}")
    kw_tokens = set(rec.tokens)
    head_tokens = set(content_tokens(c.title + " " + c.h1))
    # allow simple plural/singular variation (pdf/pdfs, receipt/receipts)
    covered = {t for t in kw_tokens if t in head_tokens or t + "s" in head_tokens or t.rstrip("s") in head_tokens}
    coverage = len(covered) / max(len(kw_tokens), 1)
    if coverage < q["min_keyword_coverage"]:
        errors.append(f"title+H1 cover only {coverage:.0%} of the keyword terms (missing: {sorted(kw_tokens - covered)})")
    _count("steps", c.steps, 3, 8, errors)
    _count("alternatives", c.alternatives, 2, 4, errors)
    _count("feature_points", c.feature_points, 3, 5, errors)
    _count("tips", c.tips, 3, 5, errors)
    _count("faqs", c.faqs, 4, 6, errors)
    for h in (c.steps_heading, c.alternatives_heading, c.features_heading, c.tips_heading):
        if h.strip().lower() in GENERIC_HEADINGS:
            errors.append(f"heading '{h}' is generic; make it specific to this keyword")

    # ---- grounding: features must exist and pro features must not be called free ----------
    for fp in c.feature_points:
        f = features.get(fp.feature_id)
        if not f:
            errors.append(f"feature_points uses unknown feature id '{fp.feature_id}'")
        elif f["tier"] == "pro" and re.search(r"\bfree\b", fp.benefit.lower()) and "not free" not in fp.benefit.lower():
            errors.append(f"'{f['name']}' is a Pro feature but its benefit text says free")
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
            errors.append(f"banned filler phrase: '{b}'")
    for pat in OVERCLAIMS:
        m = re.search(pat, lower)
        if m:
            errors.append(f"over-claim '{m.group(0)}' - remove or qualify it")

    # ---- duplicate content vs the whole published corpus -------------------------------
    sh = shingles(text)
    sents = sentences(text)
    max_j, max_j_slug, max_cos, max_cos_slug = 0.0, None, 0.0, None
    by_slug = {o["slug"]: o for o in corpus}
    title_tokens = set(content_tokens(c.title))
    for other in corpus:
        j = jaccard(sh, other["shingles"])
        if j > max_j:
            max_j, max_j_slug = j, other["slug"]
        shared = sents & sentences(other["body_text"])
        for s in shared:
            overlaps.append(f"[{other['slug']}] {s}")
        if embedding is not None and other["embedding"] is not None:
            cos = float(np.dot(embedding, other["embedding"]))
            if cos > max_cos:
                max_cos, max_cos_slug = cos, other["slug"]
        ot = set(content_tokens(other["title"]))
        if title_tokens and jaccard(title_tokens, ot) >= 0.8:
            errors.append(f"title is near-identical to /{other['slug']}: '{other['title']}'")
        if c.meta_description.strip().lower() == other["meta"].strip().lower():
            errors.append(f"meta description duplicates /{other['slug']}")
    # FAQ-level overlap: two pages answering the same question is cannibalisation in miniature,
    # even when the surrounding pages are different (caught on the OCR/image-to-text pair)
    for f in c.faqs:
        qt = set(content_tokens(f.question)) - FAQ_NOISE
        for other in corpus:
            for oq in other.get("faqs", []):
                ot = set(content_tokens(oq)) - FAQ_NOISE
                if qt and ot and jaccard(qt, ot) >= d["faq_max_overlap"]:
                    errors.append(f"FAQ '{f.question}' duplicates /{other['slug']}'s FAQ '{oq}'; "
                                  "replace it with a question only this page's searcher would ask")
    if faq_embeddings is not None and len(faq_embeddings):
        for other in corpus:
            oe = other.get("faq_emb")
            if oe is None:
                continue
            sims = faq_embeddings @ oe.T
            for x, y in zip(*np.where(sims >= d["faq_max_cosine"])):
                errors.append(f"FAQ '{c.faqs[x].question}' asks the same thing as /{other['slug']}'s "
                              f"'{other['faqs'][y]}' (cosine {sims[x, y]:.3f}); link to that page instead "
                              "and ask a question specific to this page")
    if max_j > d["page_max_jaccard"]:
        errors.append(f"too much text overlap with /{max_j_slug} (5-gram Jaccard {max_j:.3f} > {d['page_max_jaccard']})")
    if len(overlaps) > d["max_shared_sentences"]:
        errors.append(f"{len(overlaps)} sentences appear verbatim on other pages; rewrite them: "
                      + " | ".join(o[:140] for o in overlaps[:6]))
    if max_cos > d["page_max_cosine"]:
        o = by_slug[max_cos_slug]
        errors.append(
            f"semantically too close to /{max_cos_slug} (cosine {max_cos:.3f} > {d['page_max_cosine']}). "
            f"That page already covers: {' / '.join(o['headings'])}. Do not re-explain what it explains; "
            f"mention it in one sentence and link. Rebuild the steps, tips and FAQs around this page's own "
            f"angle: {brief.unique_angle}")

    return QAResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        overlaps=overlaps,
        metrics={
            "words": n_words,
            "keyword_coverage": round(coverage, 2),
            "max_jaccard": round(max_j, 4), "max_jaccard_with": max_j_slug,
            "max_cosine": round(max_cos, 4), "max_cosine_with": max_cos_slug,
            "shared_sentences": len(overlaps),
        },
    )
