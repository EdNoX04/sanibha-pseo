"""The agent pipeline, end to end.

    keywords.csv
       │  1 ingest      rules -> KeywordRecord (slug, intent guess, formats, candidate features)
       │  2 cluster     embeddings (cached) -> merge near-identical keywords, siblings, link candidates
       │  3 plan        LLM, one call per sibling batch -> PageBrief (angle, scope, exclusions)
       │  4 write       LLM -> PageContent; parallel workers, never two siblings at once
       │  5 QA          rules + similarity vs the in-memory corpus index + LLM editor
       │     └── fail -> field-local errors: PATCH only those fields (small, fast call)
       │              page-level errors: full REWRITE with the reasons ... up to max_revisions
       │              -> quarantine
       │  6 sweep       vectorised all-pairs check of the final corpus; offenders are revised
       │  7 render      HTML / Markdown / JSON / index / sitemap
       └  8 report      run report, alerts, heartbeat, exit code

State lives in SQLite, so each run is incremental: a page is regenerated only when its inputs
(keyword, app facts, the platform facts it cites, prompts, primary models) change.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback

import numpy as np
import yaml

from .agents import Agents
from .cluster import cluster_keywords, plan_batches
from .index import CorpusIndex
from .keywords import load_app_facts, load_keywords
from .llm import Provider
from .models import KeywordRecord, PageBrief, PageContent, QAResult
from .ops import alert, heartbeat, run_lock
from .quality import check_page, page_text
from .render import render_all
from .report import write_report
from .similarity import jaccard, shingles
from .store import Store

log = logging.getLogger("pseo")


class Pipeline:
    def __init__(self, cfg, provider: Provider):
        self.cfg = cfg
        self.p = provider
        self.store = Store(cfg.path("state_db"))
        self.apps = load_app_facts(cfg.path("apps_dir"))
        self.platform_facts = yaml.safe_load(cfg.path("platform_facts").read_text())["facts"]
        self.agents = Agents(provider, cfg, self.apps, self.platform_facts)
        self.stats = {"planned": 0, "written": 0, "revisions": 0, "patches": 0, "passed": 0,
                      "quarantined": 0, "failed": 0, "skipped_unchanged": 0, "merged": 0, "retired": 0,
                      "sweep_requeued": 0}
        self.consecutive_failures = 0
        self.aborted: str | None = None
        self.index: CorpusIndex | None = None
        self._lock = threading.Lock()
        self.timings: dict[str, float] = {}
        self._t = time.time()

    # ------------------------------------------------------------------ helpers
    def _mark(self, stage: str) -> None:
        """Wall-clock seconds spent in each stage, for the run report."""
        now = time.time()
        self.timings[stage] = round(self.timings.get(stage, 0) + now - self._t, 1)
        self._t = now
    def _stat(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] += n

    def _input_hash(self, rec: KeywordRecord, fact_ids: list[str] | None = None) -> str:
        """Everything that determines a page's content. fact_ids = the platform facts the page's
        brief uses (None for a page that has no brief yet -> whole fact file)."""
        h = hashlib.sha256()
        h.update(rec.model_dump_json(exclude={"cluster_id", "siblings"}).encode())
        h.update((self.cfg.path("apps_dir") / f"{rec.app_id}.yaml").read_bytes())
        if fact_ids is None:
            h.update(self.cfg.path("platform_facts").read_bytes())
        else:
            used = sorted((f for f in self.platform_facts if f["id"] in set(fact_ids)), key=lambda f: f["id"])
            h.update(json.dumps([sorted(fact_ids), used], sort_keys=True).encode())
        h.update(self.agents.prompt_version().encode())
        llm = self.cfg["llm"]
        # only the PRIMARY model of each chain: adding a fallback model must not regenerate pages
        primary = [llm[k].split(",")[0].strip() for k in ("planner_model", "writer_model", "judge_model")]
        h.update("|".join(primary).encode())
        return h.hexdigest()[:16]

    def _embed_cached(self, texts: list[str]) -> np.ndarray:
        """Embeddings for short, repeated texts (keywords): served from the DB cache, so a daily
        run with an unchanged keyword list makes no embedding call at all."""
        model = self.cfg["llm"]["embed_model"]
        keys = [hashlib.sha1(f"{model}\n{t}".encode()).hexdigest() for t in texts]
        have = self.store.cached_vectors(keys)
        missing = [i for i, k in enumerate(keys) if k not in have]
        if missing:
            vecs = self.p.embed([texts[i] for i in missing], model=model)
            new = {keys[i]: v for i, v in zip(missing, vecs)}
            self.store.cache_vectors(new)
            have.update(new)
        return np.stack([have[k] for k in keys])

    def _embed_page(self, content: PageContent) -> tuple[np.ndarray, np.ndarray]:
        """One embed call: row 0 = whole page, rows 1.. = each FAQ question."""
        text = " ".join(page_text(content).split()[:1500])  # stay under the embed input limit
        vecs = self.p.embed([text] + [f.question for f in content.faqs], model=self.cfg["llm"]["embed_model"])
        return vecs[0], vecs[1:]

    def _backfill_faq_embeddings(self) -> None:
        """Pages published before semantic FAQ checks existed get their FAQ vectors (cheap)."""
        for row in self.store.pages("passed"):
            if row["faq_embeddings"] is None and row["content_json"]:
                c = PageContent.model_validate_json(row["content_json"])
                vecs = self.p.embed([f.question for f in c.faqs], model=self.cfg["llm"]["embed_model"])
                self.store.upsert(row["slug"], faq_embeddings=vecs)

    # ------------------------------------------------------------------ main entry
    def run(self, *, force: bool = False, only: list[str] | None = None) -> dict:
        cfg = self.cfg
        with run_lock(cfg.path("lock_file")):
            heartbeat(cfg, "start")
            run_id = self.store.start_run()
            t0 = time.time()
            try:
                result = self._run(force=force, only=only)
                status = result["status"]
            except Exception as e:
                tb = traceback.format_exc()
                self.store.event(None, "run", "error", tb)
                self.store.finish_run("crashed", {**self.stats, "error": str(e)})
                alert(cfg, f"pSEO run #{run_id} CRASHED", f"{e}\n\n{tb[-1500:]}")
                heartbeat(cfg, "fail")
                raise
            result["duration_s"] = round(time.time() - t0, 1)
            result["usage"] = self.p.usage.as_dict()
            self.store.finish_run(status, {**self.stats, "usage": result["usage"], "duration_s": result["duration_s"]})
            try:
                write_report(self.store, cfg, run_id, result, self.stats)
            except Exception as e:  # a reporting bug must not hide a good run, but must be visible
                log.exception("report failed")
                result["status"] = "degraded"
                result.setdefault("problems", []).append(f"report generation failed: {e}")
            if status != "ok":
                alert(cfg, f"pSEO run #{run_id} {status.upper()}",
                      json.dumps(self.stats) + "\n" + "\n".join(result.get("problems", [])[:20]))
                heartbeat(cfg, "fail")
            else:
                heartbeat(cfg)
            return result

    def _run(self, *, force: bool, only: list[str] | None) -> dict:
        cfg, store = self.cfg, self.store
        problems: list[str] = []
        self._t = time.time()

        # 1 ingest ------------------------------------------------------------------------
        records, ingest_problems = load_keywords(cfg.path("keywords"), self.apps)
        problems += ingest_problems
        for p in ingest_problems:
            store.event(None, "ingest", "warning", p)
        if not records:
            raise RuntimeError("keyword list is empty or unreadable")
        log.info("ingested %d keywords", len(records))

        # pages whose keyword was removed from the list are retired (a real site would 301 them)
        wanted = {r.slug for r in records}
        for p in store.pages():
            if p["slug"] not in wanted and p["status"] != "retired":
                store.upsert(p["slug"], status="retired")
                self._stat("retired")

        # 2 cluster -----------------------------------------------------------------------
        kw_emb = self._embed_cached([r.keyword for r in records])
        cl = cluster_keywords(records, kw_emb, merge_cos=cfg["dedupe"]["keyword_merge_cosine"],
                              sibling_cos=cfg["dedupe"]["keyword_sibling_cosine"],
                              neighbours=cfg["pipeline"]["related_neighbours"])
        self.cluster_report = cl["report"]
        for kw, into, cos in cl["report"]["merged"]:
            slug = next(r.slug for r in records if r.keyword == kw)
            store.upsert(slug, keyword=kw, app_id=records[0].app_id, status="merged",
                         error=f"merged into /{into} (keyword cosine {cos})")
            store.event(slug, "cluster", "info", f"merged into {into}")
            self._stat("merged")
        live: list[KeywordRecord] = cl["live"]
        by_slug = {r.slug: r for r in live}
        keyword_of = {r.slug: r.keyword for r in live}

        # decide what needs (re)generation ------------------------------------------------
        todo: list[KeywordRecord] = []
        for r in live:
            row = store.get(r.slug)
            fact_ids = PageBrief.model_validate_json(row["brief_json"]).platform_fact_ids \
                if row and row["brief_json"] else None
            h = self._input_hash(r, fact_ids)
            if only and r.slug not in only and r.keyword not in only:
                continue
            if not force and row and row["status"] == "passed" and row["input_hash"] == h:
                self._stat("skipped_unchanged")
                continue
            todo.append(r)
        todo = todo[: cfg["pipeline"]["max_new_pages_per_run"]]
        log.info("%d pages to generate, %d unchanged", len(todo), self.stats["skipped_unchanged"])
        self._mark("ingest+cluster")

        # corpus index of everything already published (pages being regenerated are left out,
        # so a page is never compared with its own previous version)
        if todo:
            self._backfill_faq_embeddings()
        self.index = CorpusIndex.from_store(store, cfg)
        for r in todo:
            self.index.remove(r.slug)

        # 3 plan --------------------------------------------------------------------------
        briefs: dict[str, PageBrief] = {}
        todo_slugs = {r.slug for r in todo}
        for p in store.pages("passed"):
            if p["brief_json"] and p["slug"] in by_slug and p["slug"] not in todo_slugs:
                briefs[p["slug"]] = PageBrief.model_validate_json(p["brief_json"])
        published_briefs = dict(briefs)
        planned_ok: list[str] = []
        batches = plan_batches(todo, cfg["pipeline"]["plan_batch_size"])

        def plan_one(batch):
            log.info("planning batch of %d: %s", len(batch), ", ".join(r.slug for r in batch))
            try:
                return batch, self.agents.plan(batch, cl["neighbour_slugs"], keyword_of, published_briefs), None
            except Exception as e:  # a failed batch fails its pages, not the whole run
                return batch, None, e

        # Batches are separate keyword clusters, so they are planned concurrently: at 500
        # keywords that is ~45 planning calls, which would otherwise run one after another.
        from concurrent.futures import ThreadPoolExecutor

        n = max(1, min(int(cfg["pipeline"]["workers"]), len(batches) or 1))
        with ThreadPoolExecutor(n) as ex:
            planned = list(ex.map(plan_one, batches))
        for batch, new, err in planned:
            if err is not None:
                for r in batch:
                    self._fail(r, err)
                if self.consecutive_failures >= 3:
                    self.aborted = f"planning failed repeatedly, generation stopped: {err}"
                continue
            self.consecutive_failures = 0
            log.info("planned: %s", "; ".join(f"{s} -> {b.intent_type}" for s, b in new.items()))
            briefs.update(new)
            planned_ok += [r.slug for r in batch if r.slug in new]
            self._stat("planned", len(batch))
        todo_planned = [] if self.aborted else [r for r in todo if r.slug in set(planned_ok)]
        self._mark("plan")

        # 4-5 write + QA loop, in parallel ------------------------------------------------
        self._schedule([(r, None) for r in todo_planned], briefs, by_slug)
        self._mark("write+check")

        # 6 sweep: all-pairs check over the final corpus ----------------------------------
        requeue = self._sweep() if not self.aborted else {}
        if requeue:
            self._stat("sweep_requeued", len(requeue))
            self._schedule([(by_slug[s], why) for s, why in requeue.items() if s in by_slug], briefs, by_slug)
            self._sweep(final=True)
        self._mark("sweep")

        # 7 render ------------------------------------------------------------------------
        run_row = store.recent_runs(1)[0]
        rendered = render_all(store, cfg, self.apps, {"run_id": run_row["id"], "started_at": run_row["started_at"],
                                                      "stats": self.stats})

        self._mark("render")

        # 8 status ------------------------------------------------------------------------
        attempted = len(todo)
        bad = self.stats["quarantined"] + self.stats["failed"]
        rate = bad / attempted if attempted else 0.0
        status = "ok"
        if self.aborted:
            problems.insert(0, self.aborted)
        if self.stats["failed"]:
            status = "degraded"
            problems.append(f"{self.stats['failed']} page(s) failed with errors")
        if rate > cfg["quality"]["max_quarantine_rate"]:
            status = "degraded"
            problems.append(f"{rate:.0%} of attempted pages quarantined/failed "
                            f"(threshold {cfg['quality']['max_quarantine_rate']:.0%})")
        if self.aborted:
            status = "aborted"
        for p in store.pages("quarantined"):
            problems.append(f"quarantined /{p['slug']}: {(p['error'] or '')[:200]}")
        for p in store.pages("failed"):
            problems.append(f"failed /{p['slug']}: {(p['error'] or '')[:200]}")
        return {"status": status, "attempted": attempted, "rendered": rendered,
                "problems": problems, "cluster": self.cluster_report, "timings": self.timings}

    # ------------------------------------------------------------------ scheduler
    def _schedule(self, items: list[tuple[KeywordRecord, dict | None]], briefs: dict, by_slug: dict) -> None:
        """Run pages on `workers` threads, never two siblings at the same time.

        Pages that share territory (siblings, or a topic one of them owns) must see each other's
        final text, so they are serialised; everything else runs concurrently. With 4 workers the
        12 brief keywords finish in roughly a third of the sequential time, and the corpus sweep
        still catches anything that slips through.
        """
        if not items:
            return
        conflicts: dict[str, set[str]] = {}
        for rec, _ in items:
            b = briefs.get(rec.slug)
            near = set(rec.siblings) | ({e.owner_slug for e in b.exclusions} if b else set())
            conflicts.setdefault(rec.slug, set()).update(near)
            for s in near:                       # symmetric: if A avoids B, B avoids A
                conflicts.setdefault(s, set()).add(rec.slug)
        pending = list(items)
        in_flight: set[str] = set()
        cond = threading.Condition()

        def worker() -> None:
            while True:
                with cond:
                    while True:
                        if self.aborted or not pending:
                            return
                        pick = next((it for it in pending if not (conflicts.get(it[0].slug, set()) & in_flight)), None)
                        if pick:
                            pending.remove(pick)
                            in_flight.add(pick[0].slug)
                            break
                        cond.wait(timeout=1.0)
                rec, sweep_feedback = pick
                try:
                    self._work(rec, briefs, by_slug, sweep_feedback)
                finally:
                    with cond:
                        in_flight.discard(rec.slug)
                        cond.notify_all()

        n = max(1, int(self.cfg["pipeline"]["workers"]))
        threads = [threading.Thread(target=worker, name=f"pseo-worker-{i}", daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _work(self, rec: KeywordRecord, briefs: dict, by_slug: dict, sweep_feedback: dict | None) -> None:
        try:
            self._generate_page(rec, briefs, by_slug, sweep_feedback)
            with self._lock:
                self.consecutive_failures = 0
        except Exception as e:  # one broken page must not stop the run
            self._fail(rec, e)
            with self._lock:
                if self.consecutive_failures >= 3 and not self.aborted:
                    # circuit breaker: stop spending calls on a systemic failure (revoked key,
                    # quota gone), but still publish what passed and report what didn't
                    self.aborted = ("3 consecutive page failures, generation stopped (circuit breaker). "
                                    f"Last error: {type(e).__name__}: {e}")
                    self.store.event(None, "run", "error", self.aborted)

    # ------------------------------------------------------------------ one page
    def _siblings(self, rec: KeywordRecord, brief: PageBrief, briefs: dict, by_slug: dict) -> list[dict]:
        """What the writer must not repeat: each sibling's angle, and - if it is already
        published - its section headings and FAQ questions. Giving these up front prevents
        overlap instead of catching it afterwards (fewer revision round-trips)."""
        out = []
        for s in dict.fromkeys(rec.siblings + [e.owner_slug for e in brief.exclusions]):
            if s not in by_slug:
                continue
            e = self.index.get(s) if self.index else None
            out.append({"slug": s, "keyword": by_slug[s].keyword,
                        "angle": briefs[s].unique_angle if s in briefs else "its own keyword",
                        "headings": (e or {}).get("headings", []), "faqs": (e or {}).get("faqs", [])})
        return out

    def _generate_page(self, rec: KeywordRecord, briefs: dict[str, PageBrief], by_slug: dict,
                       sweep_feedback: dict | None = None) -> None:
        cfg, store = self.cfg, self.store
        brief = briefs[rec.slug]
        app = self.apps[rec.app_id]
        siblings = self._siblings(rec, brief, briefs, by_slug)
        content: PageContent | None = None
        feedback: list[str] = []
        fix_fields: list[str] = []
        if sweep_feedback:  # revising an existing page flagged by the corpus sweep
            row = store.get(rec.slug)
            if row and row["content_json"]:
                content = PageContent.model_validate_json(row["content_json"])
            feedback = [sweep_feedback["why"]]
            fix_fields = sweep_feedback["fields"] or []
        qa = QAResult(passed=False)
        judge = None
        max_rev = cfg["pipeline"]["max_revisions"]
        common: dict = {}

        for attempt in range(max_rev + 1):
            if content is None:
                content = self.agents.write(rec, brief, siblings)
                self._stat("written")
                mode = "draft"
            elif fix_fields:
                content = self.agents.patch(rec, brief, content, fix_fields, feedback, siblings)
                self._stat("patches")
                mode = f"patch[{','.join(fix_fields)}]"
            else:
                content = self.agents.write(rec, brief, siblings, feedback=feedback, previous=content)
                self._stat("revisions")
                mode = "rewrite"
            writer_model = getattr(self.p, "last_model", None)
            emb, faq_emb = self._embed_page(content)
            qa = check_page(content, rec, brief, app, self.index, emb, cfg, faq_emb)
            qa.metrics["writer_model"] = writer_model
            qa.metrics["mode"] = mode
            judge = None
            if qa.passed and cfg["quality"]["judge_enabled"]:
                judge = self.agents.judge(rec, brief, content, [s["keyword"] for s in siblings])
                qa.metrics["judge_model"] = getattr(self.p, "last_model", None)
                lo = cfg["quality"]["judge_min_score"]
                if (judge.specificity < lo or judge.usefulness < lo or judge.accuracy_risk < lo
                        or judge.swap_test.strip().lower() != "pass"):
                    qa.passed = False
                    qa.page_level, qa.fix_fields = True, []   # editor problems need a real rewrite
                    qa.errors.append(f"editor scores: specificity {judge.specificity}, usefulness "
                                     f"{judge.usefulness}, accuracy {judge.accuracy_risk}, swap test {judge.swap_test}")
                    qa.errors += [f"editor: {pr}" for pr in judge.problems]
            store.event(rec.slug, "qa", "info" if qa.passed else "warning",
                        f"attempt {attempt + 1} ({mode}): {'PASS' if qa.passed else '; '.join(qa.errors)[:1000]}")
            log.info("%-40s attempt %d %-22s %s %s", rec.slug, attempt + 1, mode,
                     "PASS" if qa.passed else "FAIL", json.dumps(qa.metrics))
            common = dict(keyword=rec.keyword, app_id=rec.app_id,
                          input_hash=self._input_hash(rec, brief.platform_fact_ids),
                          record_json=rec.model_dump_json(), brief_json=brief.model_dump_json(),
                          content_json=content.model_dump_json(), body_text=page_text(content),
                          qa_json=qa.model_dump_json(),
                          judge_json=judge.model_dump_json() if judge else None, attempts=attempt + 1)
            if qa.passed:
                body = page_text(content)
                sh = shingles(body)
                store.upsert(rec.slug, status="passed", error=None, shingles=sh, embedding=emb,
                             faq_embeddings=faq_emb, **common)
                self.index.put_page(rec.slug, content, body, sh, emb, faq_emb)
                self._stat("passed")
                return
            feedback = qa.errors
            patching = cfg["pipeline"].get("patch_revisions", True)
            fix_fields = [] if (qa.page_level or not patching) else qa.fix_fields

        store.upsert(rec.slug, status="quarantined", error="; ".join(qa.errors)[:2000],
                     shingles=None, embedding=None, **common)
        self.index.remove(rec.slug)
        self._stat("quarantined")

    def _fail(self, rec: KeywordRecord, e: Exception) -> None:
        with self._lock:
            self.consecutive_failures += 1
            self.stats["failed"] += 1
        msg = f"{type(e).__name__}: {e}"
        log.error("%s failed: %s", rec.slug, msg)
        log.debug("%s traceback:\n%s", rec.slug, traceback.format_exc())
        self.store.event(rec.slug, "write", "error", msg[:1000])
        self.store.upsert(rec.slug, keyword=rec.keyword, app_id=rec.app_id, status="failed", error=msg[:2000])
        if self.index:
            self.index.remove(rec.slug)

    # ------------------------------------------------------------------ corpus sweep
    def _sweep(self, final: bool = False) -> dict[str, dict]:
        """Check every published page against every other. Pages written in parallel never saw
        each other, and thresholds may have changed since older pages were published; this
        catches both. Meaning similarity is one matrix product over the whole corpus; the
        word-level checks run on candidate pairs only (all pairs while the corpus is small).
        Returns {slug: {"why", "fields"}} for the newer page of each violating pair."""
        d = self.cfg["dedupe"]
        idx = self.index or CorpusIndex.from_store(self.store, self.cfg)
        entries = idx.snapshot()
        rows = {p["slug"]: p for p in self.store.pages("passed")}
        slugs = [e["slug"] for e in entries]
        pos = {s: i for i, s in enumerate(slugs)}
        has_emb = all(e.get("embedding") is not None for e in entries)
        C = (np.stack([e["embedding"] for e in entries]) @ np.stack([e["embedding"] for e in entries]).T
             if entries and has_emb else None)
        offenders: dict[str, dict] = {}
        for i, a in enumerate(entries):
            cands = idx.candidates(a["shingles"], a.get("embedding"), exclude=a["slug"]) \
                if len(entries) >= idx.exact_below else entries[i + 1:]
            for b in cands:
                k = pos.get(b["slug"])
                if k is None or k <= i:
                    continue
                j = jaccard(a["shingles"], b["shingles"])
                shared = len(a["sentences"] & b["sentences"])
                cos = float(C[i, k]) if C is not None else 0.0
                faq_dups = []
                if a.get("faq_emb") is not None and b.get("faq_emb") is not None and len(a["faq_emb"]) and len(b["faq_emb"]):
                    fs = a["faq_emb"] @ b["faq_emb"].T
                    faq_dups = [(a["faqs"][x], b["faqs"][y], float(fs[x, y]))
                                for x, y in zip(*np.where(fs >= d["faq_max_cosine"]))]
                page_bad = j > d["page_max_jaccard"] or shared > d["max_shared_sentences"] or cos > d["page_max_cosine"]
                if not (page_bad or faq_dups):
                    continue
                a_newer = (rows.get(a["slug"], {}).get("updated_at") or 0) >= (rows.get(b["slug"], {}).get("updated_at") or 0)
                newer, other = (a, b) if a_newer else (b, a)
                why = (f"sweep: too similar to /{other['slug']} "
                       f"(jaccard {j:.3f}, shared sentences {shared}, cosine {cos:.3f})")
                for qa_, qb_, s_ in faq_dups:
                    mine, theirs = (qa_, qb_) if a_newer else (qb_, qa_)
                    why += (f"; FAQ '{mine}' asks the same thing as /{other['slug']}'s '{theirs}' "
                            f"(cosine {s_:.3f}); replace it with a question specific to this page")
                # only duplicated FAQs -> a cheap patch of the faqs field; anything else -> rewrite
                offenders[newer["slug"]] = {"why": why, "fields": None if page_bad else ["faqs"]}
        for slug, info in offenders.items():
            log.warning("%s %s", slug, info["why"][:300])
            self.store.event(slug, "sweep", "warning", info["why"])
            if final:
                self.store.upsert(slug, status="quarantined", error=info["why"])
                idx.remove(slug)
                self._stat("quarantined")
            else:
                self.store.upsert(slug, status="needs_revision", error=info["why"])
                idx.remove(slug)
        return offenders
