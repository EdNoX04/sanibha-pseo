"""Tests run fully offline with the mock provider (no API key needed in CI)."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from pseo.config import ROOT, load_config
from pseo.keywords import build_record, guess_intent, load_app_facts, slugify
from pseo.llm import MockProvider
from pseo.pipeline import Pipeline
from pseo.similarity import MinHashLSH, jaccard, sentences, shingles

APPS = load_app_facts(ROOT / "data" / "apps")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k, v in {"STATE_DB": "state.sqlite", "OUTPUT_DIR": "out", "LOCK_FILE": "lock"}.items():
        monkeypatch.setenv(f"PSEO_PATHS_{k}", str(tmp_path / v))
    monkeypatch.delenv("PSEO_ALERT_WEBHOOK", raising=False)
    c = load_config()
    c.raw["llm"]["provider"] = "mock"
    c.raw["pipeline"]["workers"] = 1   # deterministic order; parallelism has its own test
    return c


# ----------------------------------------------------------------- stage 1: rules
@pytest.mark.parametrize("kw,intent", [
    ("best free PDF scanner app", "comparison"),
    ("scan document without an app", "no_app_alternative"),
    ("remove shadow from scanned document", "problem_fix"),
    ("convert PDF to Word free", "conversion"),
    ("convert scanned image to text", "conversion"),
    ("scan receipt on iPhone", "task_howto"),
])
def test_intent_rules(kw, intent):
    assert guess_intent(kw) == intent


def test_record_derivation():
    r = build_record("convert PDF to Word free", "doc-scanner", APPS["doc-scanner"]["features"])
    assert r.slug == "convert-pdf-to-word-free"
    assert (r.source_format, r.target_format) == ("pdf", "word")
    assert r.wants_free and r.platform == "any"
    assert "word_export" in r.candidate_features
    assert slugify("  OCR handwriting iPhone ") == "ocr-handwriting-iphone"


# ----------------------------------------------------------------- similarity primitives
def test_similarity_signals():
    a = "Open the app and tap scan. Hold the phone flat above the receipt so the edges are visible."
    b = a.replace("receipt", "passport")
    c = "Compress the PDF using the balanced preset, then check the size preview before you email it."
    assert jaccard(shingles(a), shingles(b)) > 0.3
    assert jaccard(shingles(a), shingles(c)) == 0
    assert sentences(a) & sentences(a)
    lsh = MinHashLSH()
    lsh.add("a", lsh.signature(shingles(a * 3)))
    assert "a" in lsh.query(lsh.signature(shingles(a * 3)))


# ----------------------------------------------------------------- end-to-end
def test_full_run_produces_all_pages(cfg):
    res = Pipeline(cfg, MockProvider()).run()
    assert res["status"] == "ok"
    pages = cfg.path("output_dir") / "pages"
    html = sorted(p.stem for p in pages.glob("*.html") if p.stem != "index")
    assert len(html) == 12
    manifest = json.loads((cfg.path("output_dir") / "manifest.json").read_text())
    assert {m["slug"] for m in manifest} == set(html)
    one = (pages / "sign-pdf-on-iphone-free.html").read_text()
    assert '<link rel="canonical"' in one and '"FAQPage"' in one
    assert (cfg.path("output_dir") / "report.md").exists()


def test_second_run_is_incremental(cfg):
    Pipeline(cfg, MockProvider()).run()
    p2 = Pipeline(cfg, MockProvider())
    res = p2.run()
    assert p2.stats["skipped_unchanged"] == 12 and res["attempted"] == 0
    assert p2.p.usage.by_task.get("write", 0) == 0


def test_duplicate_drafts_are_caught_and_revised(cfg, monkeypatch):
    """The mock writes the same body for every page on the first draft. QA must reject every
    page after the first, and the revise loop must fix them."""
    pipe = Pipeline(cfg, MockProvider(misbehave="duplicate"))
    res = pipe.run()
    assert pipe.stats["revisions"] >= 10
    assert res["status"] == "ok"
    corpus = pipe.store.corpus()
    for i, a in enumerate(corpus):
        for b in corpus[i + 1:]:
            assert jaccard(a["shingles"], b["shingles"]) <= cfg["dedupe"]["page_max_jaccard"]


def test_unfixable_page_is_quarantined_not_published(cfg):
    """If the writer keeps producing duplicates, the page is quarantined and the run degrades."""

    class Stubborn(MockProvider):
        def generate(self, **kw):
            if kw["task"] == "revise":
                kw["task"] = "write"   # ignore feedback: keep returning the duplicate
            return super().generate(**kw)

    pipe = Pipeline(cfg, Stubborn(misbehave="duplicate"))
    res = pipe.run()
    assert res["status"] == "degraded"
    published = {p.stem for p in (cfg.path("output_dir") / "pages").glob("*.html")} - {"index"}
    assert len(published) == 1       # only the very first page; the rest are held back
    assert any((cfg.path("output_dir") / "quarantine").glob("*.json"))


def test_changed_facts_trigger_regeneration(cfg, tmp_path):
    Pipeline(cfg, MockProvider()).run()
    pipe = Pipeline(cfg, MockProvider())
    pipe.platform_facts = pipe.platform_facts  # same facts -> nothing to do
    assert pipe.run()["attempted"] == 0
    # change the prompt version: every page's input hash changes
    pipe2 = Pipeline(cfg, MockProvider())
    pipe2.agents.prompt_version = lambda: "changed"
    assert pipe2.run()["attempted"] == 12


def test_keyword_merge(cfg):
    from pseo.cluster import cluster_keywords

    recs = [build_record(k, "doc-scanner", APPS["doc-scanner"]["features"])
            for k in ["sign PDF on iPhone free", "sign a PDF on iPhone for free", "compress PDF file size free"]]
    emb = MockProvider().embed([r.keyword for r in recs], model="x")
    emb[1] = emb[0]  # force the two "sign" keywords to be identical in embedding space
    out = cluster_keywords(recs, emb, merge_cos=0.96, sibling_cos=0.8)
    assert len(out["live"]) == 2
    assert recs[0].merged_keywords == ["sign a PDF on iPhone for free"]


# ----------------------------------------------------------------- provider resilience
def test_model_fallback_chain():
    """Daily quota on the first model -> the call (and later calls) use the next model."""
    from pseo.llm import GeminiProvider, ModelOverloaded, QuotaExhausted, Usage

    p = object.__new__(GeminiProvider)
    import threading
    p.usage, p.exhausted, p._tls = Usage(), set(), threading.local()
    calls = []

    def fake(*, task, model, **kw):
        calls.append(model)
        if model == "a":
            raise QuotaExhausted("a out")
        if model == "b" and len(calls) < 3:
            raise ModelOverloaded("b busy")
        return model

    p._generate_one = fake
    common = dict(task="write", system="", prompt="", schema=None, temperature=0)
    assert p.generate(model="a,b,c", **common) == "c"      # a: quota, b: busy, c: ok
    assert p.generate(model="a,b,c", **common) == "b"      # a skipped for the rest of the run
    assert "a" in p.exhausted and calls.count("a") == 1


def test_incremental_plan_sees_published_siblings(cfg):
    """A keyword added later is planned with the existing pages' territory in the prompt."""
    import csv

    Pipeline(cfg, MockProvider()).run()
    kw_file = cfg.path("keywords")
    rows = list(csv.DictReader(kw_file.open()))
    new_csv = cfg.root / "tmp_keywords.csv"
    try:
        with new_csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["keyword", "app_id"])
            w.writeheader()
            w.writerows(rows + [{"keyword": "scan multiple receipts at once", "app_id": "doc-scanner"}])
        cfg.raw["paths"]["keywords"] = str(new_csv)
        seen = {}
        prov = MockProvider()
        orig = prov.generate

        def spy(**kw):
            if kw["task"] == "plan":
                seen["prompt"] = kw["prompt"]
            return orig(**kw)

        prov.generate = spy
        res = Pipeline(cfg, prov).run()
        assert res["attempted"] == 1
        assert "ALREADY-PUBLISHED RELATED PAGES" in seen["prompt"]
        assert "scan-receipt-on-iphone" in seen["prompt"].split("ALREADY-PUBLISHED")[1]
    finally:
        new_csv.unlink(missing_ok=True)


def test_circuit_breaker_stops_but_still_publishes(cfg):
    """Quota dies mid-run: generation stops after 3 failures, pages that passed are still
    rendered, and the run reports 'aborted' (exit code 1) instead of crashing."""
    from pseo.llm import QuotaExhausted

    class DiesAfterFour(MockProvider):
        def generate(self, **kw):
            if kw["task"] == "write" and self.usage.by_task.get("write", 0) >= 4:
                raise QuotaExhausted("daily quota exhausted")
            return super().generate(**kw)

    pipe = Pipeline(cfg, DiesAfterFour())
    res = pipe.run()
    assert res["status"] == "aborted"
    # with parallel workers, pages already in flight when the breaker trips also finish/fail
    assert pipe.stats["failed"] >= 3 and pipe.stats["passed"] == 4
    pages = {p.stem for p in (cfg.path("output_dir") / "pages").glob("*.html")} - {"index"}
    assert len(pages) == 4
    # next run picks up exactly the pages that never got written
    res2 = Pipeline(cfg, MockProvider()).run()
    assert res2["status"] == "ok" and res2["attempted"] == 12 - 4


def test_fact_edit_regenerates_only_pages_using_it(cfg, tmp_path):
    import yaml

    facts_path = tmp_path / "facts.yaml"
    facts = yaml.safe_load((ROOT / "data" / "platform_facts.yaml").read_text())
    facts_path.write_text(yaml.safe_dump(facts))
    cfg.raw["paths"]["platform_facts"] = str(facts_path)
    Pipeline(cfg, MockProvider()).run()

    # mock briefs use ios_notes_scan + online_converters; editing an unused fact changes nothing
    for f in facts["facts"]:
        if f["id"] == "passport_sizes":
            f["text"] += " (edited)"
    facts_path.write_text(yaml.safe_dump(facts))
    assert Pipeline(cfg, MockProvider()).run()["attempted"] == 0

    for f in facts["facts"]:
        if f["id"] == "online_converters":
            f["text"] += " (edited)"
    facts_path.write_text(yaml.safe_dump(facts))
    assert Pipeline(cfg, MockProvider()).run()["attempted"] == 12


def test_quality_gates_on_a_single_page():
    """Unit-level check of the rule gates: over-claims, banned filler, pro-as-free, FAQ overlap."""
    from pseo.config import load_config
    from pseo.models import CTA, FAQ, Alternative, FeaturePoint, PageBrief, PageContent, Step
    from pseo.quality import check_page

    cfg = load_config()
    app = APPS["doc-scanner"]
    rec = build_record("convert PDF to Word free", "doc-scanner", app["features"])
    brief = PageBrief(slug=rec.slug, intent_type="conversion", searcher_goal="g", scenario="s",
                      unique_angle="free routes vs Pro export", must_answer=[], exclusions=[],
                      feature_ids=["word_export"], platform_fact_ids=[], related_slugs=[], ambiguity_note="")
    filler = " ".join(f"word{i}" for i in range(700))
    page = PageContent(
        title="Convert PDF to Word Free", meta_description="x" * 130, h1="Convert a PDF to Word for free",
        quick_answer="This is guaranteed to work. " + filler, intro=["In today's digital world, PDFs matter."],
        steps_heading="Steps", steps=[Step(title="a", detail="b")] * 3,
        alternatives_heading="Free ways to convert", alternatives=[Alternative(method="m", how="h", best_for="b", limitation="l")] * 2,
        features_heading="Word export", feature_points=[FeaturePoint(feature_id="word_export", benefit="Totally free export.")] * 3,
        tips_heading="Conversion tips", tips=["t1", "t2", "t3"],
        faqs=[FAQ(question="Can I copy recognized text and export it as txt?", answer="a")] * 4,
        cta=CTA(heading="h", text="t"))
    from pseo.index import CorpusIndex

    index = CorpusIndex()
    index.put("other", {"slug": "other", "body_text": "", "shingles": set(), "embedding": None, "title": "Other",
                        "meta": "m", "headings": [], "faqs": ["Can you copy the recognized text or export a txt file?"]})
    qa = check_page(page, rec, brief, app, index, None, cfg)
    text = " | ".join(qa.errors)
    assert not qa.passed
    assert "over-claim" in text and "banned filler" in text and "Pro feature" in text
    assert "heading 'Steps' is generic" in text and "duplicates /other's FAQ" in text


def test_explain_tells_the_story_of_a_page(cfg):
    from pseo.explain import explain

    pipe = Pipeline(cfg, MockProvider(misbehave="duplicate"))
    pipe.run()
    outs = [explain(pipe.store, p["slug"]) for p in pipe.store.pages("passed")]
    assert all("3 PLAN" in o and "PASSED" in o and "FINAL PAGE" in o for o in outs)
    assert any("REJECTED" in o for o in outs)
    assert "convert-pdf-to-word-free" in explain(pipe.store, None)


def test_keyword_commands_edit_the_list_and_the_next_run_follows(cfg, tmp_path):
    import shutil

    from pseo import keyword_admin as ka

    kw = tmp_path / "keywords.csv"
    shutil.copy(ROOT / "data" / "keywords.csv", kw)
    cfg.raw["paths"]["keywords"] = str(kw)
    Pipeline(cfg, MockProvider()).run()

    msgs = ka.add(kw, ["save scanned documents to Google Drive", "Sign PDF on iPhone FREE"], "doc-scanner", APPS)
    assert msgs[0].startswith("added") and "skipped" in msgs[1]          # duplicate wording rejected
    with pytest.raises(ValueError):
        ka.add(kw, ["x y"], "no-such-app", APPS)
    res = Pipeline(cfg, MockProvider()).run()
    assert res["attempted"] == 1                                           # only the new keyword

    msgs = ka.replace(kw, "save-scanned-documents-to-google-drive", "save scans to Dropbox", APPS)
    assert "removed" in msgs[0] and "added" in msgs[1]
    pipe = Pipeline(cfg, MockProvider())
    res = pipe.run()
    assert res["attempted"] == 1 and pipe.stats["retired"] == 1
    pages = {p.stem for p in (cfg.path("output_dir") / "pages").glob("*.html")} - {"index"}
    assert "save-scans-to-dropbox" in pages and "save-scanned-documents-to-google-drive" not in pages
    assert "retired" in ka.listing(kw, pipe.store).lower()


def test_parallel_scheduler_is_faster_and_never_runs_siblings_together(cfg):
    """4 workers with simulated API latency: same 12 pages, less wall time, and two pages that
    share territory (siblings / topic owners) are never generated at the same moment."""
    import threading
    import time as _t

    lat = {"plan": 0.05, "write": 0.12, "revise": 0.12, "patch": 0.04, "judge": 0.04, "embed": 0.01}

    def timed(workers):
        c = load_config()
        c.raw = {**cfg.raw, "pipeline": {**cfg.raw["pipeline"], "workers": workers}}
        c.raw["paths"] = dict(cfg.raw["paths"])
        base = cfg.path("state_db").parent
        c.raw["paths"]["state_db"] = str(base / f"w{workers}.sqlite")
        c.raw["paths"]["output_dir"] = str(base / f"out{workers}")
        c.raw["paths"]["lock_file"] = str(base / f"lock{workers}")
        pipe = Pipeline(c, MockProvider(latency=lat))
        in_flight, overlaps, lock = set(), [], threading.Lock()
        orig = pipe._generate_page

        def spy(rec, briefs, by_slug, sweep_feedback=None):
            b = briefs[rec.slug]
            near = set(rec.siblings) | {e.owner_slug for e in b.exclusions}
            with lock:
                clash = {s for s in in_flight if s in near or rec.slug in pipe._near.get(s, set())}
                if clash:
                    overlaps.append((rec.slug, clash))
                in_flight.add(rec.slug)
                pipe._near[rec.slug] = near
            try:
                return orig(rec, briefs, by_slug, sweep_feedback)
            finally:
                with lock:
                    in_flight.discard(rec.slug)

        pipe._near = {}
        pipe._generate_page = spy
        t0 = _t.time()
        res = pipe.run()
        return _t.time() - t0, res, overlaps

    t1, r1, _ = timed(1)
    t4, r4, overlaps = timed(4)
    assert r1["status"] == r4["status"] == "ok"
    assert r4["rendered"]["published"] == 12
    assert not overlaps, overlaps
    assert t4 < t1 * 0.75, (t1, t4)


def test_field_local_errors_are_patched_not_rewritten(cfg, monkeypatch):
    """A draft whose only problem is a too-long meta description gets a patch of that one field."""
    from pseo import quality

    calls = []
    real = quality.check_page

    def flaky(c, rec, *a, **k):
        qa = real(c, rec, *a, **k)
        if rec.slug == "sign-pdf-on-iphone-free" and not calls:
            calls.append(1)
            qa.passed, qa.errors, qa.fix_fields, qa.page_level = False, ["meta_description is 170 chars"], ["meta_description"], False
        return qa

    monkeypatch.setattr("pseo.pipeline.check_page", flaky)
    prov = MockProvider()
    pipe = Pipeline(cfg, prov)
    assert pipe.run()["status"] == "ok"
    assert prov.usage.by_task.get("patch") == 1 and pipe.stats["patches"] == 1
    assert prov.usage.by_task.get("revise", 0) == 0
