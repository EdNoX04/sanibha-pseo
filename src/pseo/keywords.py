"""Stage 1 - ingest: raw keyword -> KeywordRecord using deterministic rules.

Rules (not an LLM) here because these fields must be stable run-to-run: the slug is a URL,
and the derived fields feed the input hash that decides whether a page is regenerated.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

import yaml

from .models import KeywordRecord

STOPWORDS = {
    "a", "an", "the", "on", "in", "into", "to", "for", "of", "with", "without", "and",
    "or", "my", "your", "how", "from", "one", "is", "it", "can", "i", "best",
}
PLATFORM_TERMS = {"iphone": "iphone", "ios": "iphone", "ipad": "iphone"}
FORMAT_TERMS = {
    "pdf": "pdf", "pdfs": "pdf", "word": "word", "docx": "word", "text": "text",
    "image": "image", "photo": "image", "jpg": "image", "contacts": "contacts",
    "receipt": "paper", "document": "paper", "card": "paper", "handwriting": "paper",
}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", normalise(text))
    return text.strip("-")


def content_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", normalise(text)) if t not in STOPWORDS]


def guess_intent(kw: str) -> str:
    k = normalise(kw)
    if re.search(r"\b(best|top|vs|review|alternative to)\b", k):
        return "comparison"
    if re.search(r"\bwithout (an |the )?app\b", k):
        return "no_app_alternative"
    if re.search(r"\b(remove|fix|blurry|shadow|crooked|faded)\b", k):
        return "problem_fix"
    if re.search(r"\bconvert\b|\bto (word|text|pdf|jpg|excel)\b", k):
        return "conversion"
    return "task_howto"


def build_record(keyword: str, app_id: str, features: list[dict]) -> KeywordRecord:
    norm = normalise(keyword)
    words = re.findall(r"[a-z0-9]+", norm)
    tokens = content_tokens(norm)
    src = next((FORMAT_TERMS[w] for w in words if w in FORMAT_TERMS), None)
    tgt = None
    if "to" in words:
        after = words[words.index("to") + 1:]
        tgt = next((FORMAT_TERMS[w] for w in after if w in FORMAT_TERMS), None)
    if src == tgt:
        tgt = None
    candidates = []
    for f in features:
        if any(trig in norm for trig in f.get("triggers", [])):
            candidates.append(f["id"])
    return KeywordRecord(
        keyword=keyword.strip(),
        app_id=app_id,
        slug=slugify(keyword),
        tokens=tokens,
        platform=next((PLATFORM_TERMS[w] for w in words if w in PLATFORM_TERMS), "any"),
        wants_free="free" in words,
        intent_guess=guess_intent(norm),
        action=words[0] if words else "",
        source_format=src,
        target_format=tgt,
        candidate_features=candidates,
    )


def load_app_facts(apps_dir: Path) -> dict[str, dict]:
    return {p.stem: yaml.safe_load(p.read_text()) for p in sorted(apps_dir.glob("*.yaml"))}


def load_keywords(csv_path: Path, apps: dict[str, dict]) -> tuple[list[KeywordRecord], list[str]]:
    """Returns (records, problems). Exact duplicates (after normalisation) are dropped here;
    near-duplicates are handled by clustering, which needs embeddings."""
    records, problems, seen = [], [], set()
    with csv_path.open(newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            kw = (row.get("keyword") or "").strip()
            app_id = (row.get("app_id") or "").strip()
            if not kw:
                continue
            if app_id not in apps:
                problems.append(f"line {i}: unknown app_id '{app_id}' for '{kw}'")
                continue
            key = (app_id, " ".join(sorted(content_tokens(kw))))
            if key in seen:
                problems.append(f"line {i}: '{kw}' duplicates an earlier keyword, skipped")
                continue
            seen.add(key)
            records.append(build_record(kw, app_id, apps[app_id]["features"]))
    return records, problems
