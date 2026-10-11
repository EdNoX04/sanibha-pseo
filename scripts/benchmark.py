"""Benchmark the pipeline's architecture without spending API quota.

Runs the real pipeline (real clustering, scheduling, QA gates, sweep, rendering) against the
offline mock LLM with *simulated* latencies taken from the live Gemini runs:

    write/rewrite (default reasoning)  ~30 s     measured median ~40 s per draft incl. embed + editor
    write with thinking=low            ~0.35x    measured 3.1 s vs 9.1 s on the same prompt
    patch (one or two fields, low)     ~6 s      output is a few hundred tokens, not a page
    editor (judge)                     ~8 s
    plan (one call per sibling batch)  ~60 s
    embed                              ~0.5 s

A first-draft failure mix like the real run is injected: some drafts fail on a field-local
problem (meta length, a banned word, a duplicated FAQ) and some on a page-level one (too close
to a sibling). Latencies are divided by SCALE so the benchmark finishes in seconds; the table
reports real-world estimates (x SCALE).

    PYTHONPATH=src python scripts/benchmark.py            # 12 brief keywords
    PYTHONPATH=src python scripts/benchmark.py --scale-test   # + 500 synthetic keywords
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pseo import pipeline as pl  # noqa: E402
from pseo.config import load_config  # noqa: E402
from pseo.llm import MockProvider  # noqa: E402

SCALE = 100.0
LAT = {"write": 30, "revise": 30, "patch": 6 / 0.35, "judge": 8 / 0.35, "plan": 60, "embed": 0.5}
# patch/judge values are divided back by 0.35 because the mock applies the "low" factor itself


def failure_mix(real_check):
    """First draft of ~1/3 of pages fails a field-local check, ~1/4 a page-level one."""
    seen: set[str] = set()

    def check(c, rec, *a, **k):
        qa = real_check(c, rec, *a, **k)
        h = int(hashlib.md5(rec.slug.encode()).hexdigest(), 16) % 12
        if rec.slug not in seen:
            seen.add(rec.slug)
            if h < 4:
                qa.passed, qa.page_level = False, False
                qa.errors, qa.fix_fields = ["meta_description is 171 chars, needs 110-160"], ["meta_description"]
            elif h < 7:
                qa.passed, qa.page_level, qa.fix_fields = False, True, []
                qa.errors = ["semantically too close to a sibling page (cosine 0.935 > 0.93)"]
        return qa

    return check


def run(label: str, keywords_csv: Path, *, workers: int, thinking: bool, patches: bool,
        mock_semantics: bool = False) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="pseo-bench-"))
    cfg = load_config()
    cfg.raw["llm"]["provider"] = "mock"
    cfg.raw["paths"].update(keywords=str(keywords_csv), state_db=str(tmp / "s.sqlite"),
                            output_dir=str(tmp / "out"), lock_file=str(tmp / "lock"))
    cfg.raw["pipeline"].update(workers=workers, patch_revisions=patches,
                               max_new_pages_per_run=100000)
    if mock_semantics:
        # The mock's embeddings are bag-of-words, not semantic: 500 template pages about
        # "password protect <document>" look ~0.93 alike to them. For the throughput test the
        # meaning-based gates are switched off; they are exercised by the real Gemini runs.
        cfg.raw["dedupe"].update(page_max_cosine=0.999, faq_max_cosine=0.999)
    cfg.raw["llm"]["thinking"] = (cfg.raw["llm"].get("thinking") if thinking else
                                  {k: "" for k in ("planner", "writer_draft", "writer_revise", "patch", "judge")})
    prov = MockProvider(latency={k: v / SCALE for k, v in LAT.items()})
    real = pl.check_page
    qa_time = [0.0, 0]

    def timed_check(*a, **k):
        t = time.perf_counter()
        try:
            return failure_mix_fn(*a, **k)
        finally:
            qa_time[0] += time.perf_counter() - t
            qa_time[1] += 1

    failure_mix_fn = failure_mix(real)
    pl.check_page = timed_check
    try:
        t0 = time.time()
        p = pl.Pipeline(cfg, prov)
        res = p.run()
        wall = time.time() - t0
    finally:
        pl.check_page = real
    u = prov.usage.as_dict()
    return {"label": label, "pages": res["rendered"]["published"], "status": res["status"],
            "est_minutes": round(wall * SCALE / 60, 1), "calls": u["calls"], "by_task": u["by_task"],
            "embed_calls": u["embed_calls"], "qa_ms_per_check": round(1000 * qa_time[0] / max(qa_time[1], 1), 1),
            "stats": {k: p.stats[k] for k in ("written", "revisions", "patches", "sweep_requeued", "quarantined")}}


def synthetic_keywords(n: int, path: Path) -> Path:
    verbs = ["scan", "convert", "sign", "compress", "merge", "rotate", "crop", "split", "annotate", "redact",
             "export", "share", "print", "fax", "archive", "translate", "OCR", "watermark", "password protect", "flatten"]
    objs = ["receipt", "invoice", "contract", "passport", "ID card", "business card", "whiteboard", "book page",
            "lecture notes", "bank statement", "tax form", "medical record", "lease", "resume", "letter",
            "school worksheet", "recipe", "photo", "blueprint", "menu", "ticket", "warranty card", "prescription",
            "report card", "certificate"]
    # distinct tasks only (20 verbs x 25 documents): near-identical phrasings such as "... on iPhone"
    # vs "... on iPad" would be merged by the keyword stage in a real run, but the offline mock's
    # bag-of-words embeddings cannot recognise them as the same intent
    rows = [{"keyword": f"{v} {o}", "app_id": "doc-scanner"} for v, o in itertools.product(verbs, objs)]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["keyword", "app_id"])
        w.writeheader()
        w.writerows(rows[:n])
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale-test", action="store_true")
    ap.add_argument("--only-scale", action="store_true")
    args = ap.parse_args()
    brief = ROOT / "data" / "keywords.csv"
    results = [] if args.only_scale else [
        run("v1: sequential, full rewrites, default reasoning", brief, workers=1, thinking=False, patches=False),
        run("v2: 4 sibling-aware workers", brief, workers=4, thinking=False, patches=False),
        run("v2: + targeted patches", brief, workers=4, thinking=False, patches=True),
        run("v2: + reasoning effort per role (final)", brief, workers=4, thinking=True, patches=True),
    ]
    if args.scale_test or args.only_scale:
        kw = synthetic_keywords(500, Path(tempfile.mkdtemp()) / "kw500.csv")
        results.append(run("500 keywords, final (8 workers)", kw, workers=8, thinking=True, patches=True,
                           mock_semantics=True))
        r = results[-1]
        # At this size the benchmark's own wall clock mixes real CPU work (QA, sweep) with scaled
        # sleeps, so also report the API time implied by the call mix, spread over 8 workers.
        low = {"write": 0.35, "patch": 0.35, "judge": 0.35}
        api_s = sum(n * LAT.get(t, 0) * low.get(t, 1.0) for t, n in r["by_task"].items()) + r["embed_calls"] * LAT["embed"]
        r["api_minutes_8_workers"] = round(api_s / 8 / 60, 1)
        r["api_minutes_sequential"] = round(api_s / 60, 1)
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
