"""The agent pipeline, end to end.

    keywords.csv
       │  1 ingest      rules -> KeywordRecord (slug, intent guess, formats, candidate features)
       │  2 cluster     embeddings -> merge near-identical keywords, mark siblings, link candidates
       │  3 plan        LLM, one call per sibling batch -> PageBrief (angle, scope, exclusions)
       │  4 write       LLM -> PageContent (structured JSON in the fixed template)
       │  5 QA          rules + similarity vs whole corpus + LLM judge
       │     └── fail -> feedback -> 4 (revise) ... up to max_revisions -> quarantine
       │  6 sweep       all-pairs similarity over the final corpus; violators get re-revised
       │  7 render      HTML / Markdown / JSON / index / sitemap
       └  8 report      run report, alerts, heartbeat, exit code

State lives in SQLite, so each run is incremental: a page is regenerated only when its inputs
(keyword, app facts, platform facts, prompts, models) change.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import yaml

from .agents import Agents
from .cluster import cluster_keywords, plan_batches
from .keywords import load_app_facts, load_keywords
from .llm import LLMError, Provider
from .models import KeywordRecord, PageBrief, PageContent, QAResult
from .ops import alert, heartbeat, run_lock
from .quality import check_page, page_text
from .render import render_all
from .report import write_report
from .similarity import jaccard, sentences, shingles
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
        self.stats = {"planned": 0, "written": 0, "revisions": 0, "passed": 0, "quarantined": 0,
                      "failed": 0, "skipped_unchanged": 0, "merged": 0, "retired": 0, "sweep_requeued": 0}
        self.consecutive_failures = 0

    # ------------------------------------------------------------------ helpers
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
            result["usage"] = vars(self.p.usage)
            self.store.finish_run(status, {**self.stats, "usage": result["usage"]})
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
                self.stats["retired"] += 1

        # 2 cluster -----------------------------------------------------------------------
        kw_emb = self.p.embed([r.keyword for r in records], model=cfg["llm"]["embed_model"])
        cl = cluster_keywords(records, kw_emb, merge_cos=cfg["dedupe"]["keyword_merge_cosine"],
                              sibling_cos=cfg["dedupe"]["keyword_sibling_cosine"],
                              neighbours=cfg["pipeline"]["related_neighbours"])
        self.cluster_report = cl["report"]
        for kw, into, cos in cl["report"]["merged"]:
            slug = next(r.slug for r in records if r.keyword == kw)
            store.upsert(slug, keyword=kw, app_id=records[0].app_id, status="merged",
                         error=f"merged into /{into} (keyword cosine {cos})")
            store.event(slug, "cluster", "info", f"merged into {into}")
            self.stats["merged"] += 1
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
                self.stats["skipped_unchanged"] += 1
                continue
            todo.append(r)
        todo = todo[: cfg["pipeline"]["max_new_pages_per_run"]]
        log.info("%d pages to generate, %d unchanged", len(todo), self.stats["skipped_unchanged"])

        # 3 plan --------------------------------------------------------------------------
        briefs: dict[str, PageBrief] = {}
        todo_slugs = {r.slug for r in todo}
        for p in store.pages("passed"):
            if p["brief_json"] and p["slug"] in by_slug and p["slug"] not in todo_slugs:
                briefs[p["slug"]] = PageBrief.model_validate_json(p["brief_json"])
        published_briefs = dict(briefs)
        planned_ok: list[str] = []
        aborted: str | None = None
        for batch in plan_batches(todo, cfg["pipeline"]["plan_batch_size"]):
            log.info("planning batch of %d: %s", len(batch), ", ".join(r.slug for r in batch))
            try:
                new = self.agents.plan(batch, cl["neighbour_slugs"], keyword_of, published_briefs)
            except Exception as e:  # a failed batch fails its pages, not the whole run
                for r in batch:
                    self._fail(r, e)
                if self.consecutive_failures >= 3:
                    aborted = f"planning failed repeatedly, generation stopped: {e}"
                    break
                continue
            self.consecutive_failures = 0
            log.info("planned: %s", "; ".join(f"{s} -> {b.intent_type}" for s, b in new.items()))
            briefs.update(new)
            planned_ok += [r.slug for r in batch if r.slug in new]
            self.stats["planned"] += len(batch)
        todo_planned = [] if aborted else [r for r in todo if r.slug in set(planned_ok)]

        # 4-5 write + QA loop -------------------------------------------------------------
        def work(rec: KeywordRecord, initial_feedback: list[str] | None = None):
            try:
                self._generate_page(rec, briefs, by_slug, initial_feedback)
                self.consecutive_failures = 0
            except LLMError as e:
                self._fail(rec, e)
            except Exception as e:  # one broken page must not stop the run
                self._fail(rec, e)

        workers = max(1, int(cfg["pipeline"]["workers"]))
        if workers == 1:
            for rec in todo_planned:
                work(rec)
                if self.consecutive_failures >= 3:
                    # circuit breaker: stop spending calls on a systemic failure (revoked key,
                    # quota gone), but still publish what passed and report what didn't
                    aborted = ("3 consecutive page failures, generation stopped (circuit breaker). "
                               "Last error: " + (store.get(rec.slug) or {}).get("error", ""))
                    store.event(None, "run", "error", aborted)
                    break
        else:
            with ThreadPoolExecutor(workers) as ex:
                list(ex.map(work, todo_planned))

        # 6 sweep: all-pairs check over the final corpus ----------------------------------
        if not aborted:
            self._backfill_faq_embeddings()
        requeue = self._sweep() if not aborted else {}
        for slug, why in requeue.items():
            rec = by_slug.get(slug)
            if rec:
                self.stats["sweep_requeued"] += 1
                work(rec, [why + ". Rewrite the overlapping parts so this page is clearly about its own keyword."])
        if requeue:
            self._sweep(final=True)

        # 7 render ------------------------------------------------------------------------
        run_row = store.recent_runs(1)[0]
        rendered = render_all(store, cfg, self.apps, {"run_id": run_row["id"], "started_at": run_row["started_at"],
                                                      "stats": self.stats})

        # 8 status ------------------------------------------------------------------------
        attempted = len(todo)
        bad = self.stats["quarantined"] + self.stats["failed"]
        rate = bad / attempted if attempted else 0.0
        status = "ok"
        if aborted:
            problems.insert(0, aborted)
        if self.stats["failed"]:
            status = "degraded"
            problems.append(f"{self.stats['failed']} page(s) failed with errors")
        if rate > cfg["quality"]["max_quarantine_rate"]:
            status = "degraded"
            problems.append(f"{rate:.0%} of attempted pages quarantined/failed "
                            f"(threshold {cfg['quality']['max_quarantine_rate']:.0%})")
        if aborted:
            status = "aborted"
        for p in store.pages("quarantined"):
            problems.append(f"quarantined /{p['slug']}: {(p['error'] or '')[:200]}")
        for p in store.pages("failed"):
            problems.append(f"failed /{p['slug']}: {(p['error'] or '')[:200]}")
        return {"status": status, "attempted": attempted, "rendered": rendered,
                "problems": problems, "cluster": self.cluster_report}

    # ------------------------------------------------------------------ one page
    def _generate_page(self, rec: KeywordRecord, briefs: dict[str, PageBrief], by_slug: dict,
                       initial_feedback: list[str] | None = None) -> None:
        cfg, store = self.cfg, self.store
        brief = briefs[rec.slug]
        app = self.apps[rec.app_id]
        sib_slugs = list(dict.fromkeys(rec.siblings + [e.owner_slug for e in brief.exclusions]))
        siblings = [{"slug": s, "keyword": by_slug[s].keyword,
                     "angle": briefs[s].unique_angle if s in briefs else "its own keyword"}
                    for s in sib_slugs if s in by_slug]
        content: PageContent | None = None
        feedback: list[str] = list(initial_feedback or [])
        if feedback:  # revising an existing page (e.g. flagged by the corpus sweep)
            row = store.get(rec.slug)
            if row and row["content_json"]:
                content = PageContent.model_validate_json(row["content_json"])
        qa = QAResult(passed=False)
        judge = None
        max_rev = cfg["pipeline"]["max_revisions"]

        for attempt in range(max_rev + 1):
            content = self.agents.write(rec, brief, siblings, feedback=feedback or None, previous=content)
            writer_model = getattr(self.p, "last_model", None)
            self.stats["revisions" if feedback else "written"] += 1
            emb, faq_emb = self._embed_page(content)
            qa = check_page(content, rec, brief, app, store.corpus(exclude=rec.slug), emb, cfg, faq_emb)
            qa.metrics["writer_model"] = writer_model
            judge = None
            if qa.passed and cfg["quality"]["judge_enabled"]:
                judge = self.agents.judge(rec, brief, content, [s["keyword"] for s in siblings])
                qa.metrics["judge_model"] = getattr(self.p, "last_model", None)
                lo = cfg["quality"]["judge_min_score"]
                if judge.specificity < lo or judge.usefulness < lo or judge.accuracy_risk < lo or judge.swap_test.strip().lower() != "pass":
                    qa.passed = False
                    qa.errors.append(f"editor scores: specificity {judge.specificity}, usefulness "
                                     f"{judge.usefulness}, accuracy {judge.accuracy_risk}, swap test {judge.swap_test}")
                    qa.errors += [f"editor: {pr}" for pr in judge.problems]
            store.event(rec.slug, "qa", "info" if qa.passed else "warning",
                        f"attempt {attempt + 1}: {'PASS' if qa.passed else '; '.join(qa.errors)[:1000]}")
            log.info("%-40s attempt %d %s %s", rec.slug, attempt + 1, "PASS" if qa.passed else "FAIL",
                     json.dumps(qa.metrics))
            common = dict(keyword=rec.keyword, app_id=rec.app_id,
                          input_hash=self._input_hash(rec, brief.platform_fact_ids),
                          record_json=rec.model_dump_json(), brief_json=brief.model_dump_json(),
                          content_json=content.model_dump_json(), body_text=page_text(content),
                          qa_json=qa.model_dump_json(),
                          judge_json=judge.model_dump_json() if judge else None, attempts=attempt + 1)
            if qa.passed:
                store.upsert(rec.slug, status="passed", error=None,
                             shingles=shingles(page_text(content)), embedding=emb,
                             faq_embeddings=faq_emb, **common)
                self.stats["passed"] += 1
                return
            feedback = qa.errors

        store.upsert(rec.slug, status="quarantined", error="; ".join(qa.errors)[:2000],
                     shingles=None, embedding=None, **common)
        self.stats["quarantined"] += 1

    def _fail(self, rec: KeywordRecord, e: Exception) -> None:
        self.consecutive_failures += 1
        self.stats["failed"] += 1
        msg = f"{type(e).__name__}: {e}"
        log.error("%s failed: %s", rec.slug, msg)
        log.debug("%s traceback:\n%s", rec.slug, traceback.format_exc())
        self.store.event(rec.slug, "write", "error", msg[:1000])
        self.store.upsert(rec.slug, keyword=rec.keyword, app_id=rec.app_id, status="failed", error=msg[:2000])

    # ------------------------------------------------------------------ corpus sweep
    def _sweep(self, final: bool = False) -> dict[str, str]:
        """Pairwise check of every published page against every other. Pages written in
        parallel never saw each other, and thresholds may have changed since older pages were
        published; this catches both. Returns slugs to regenerate (the newer page of a pair)."""
        d = self.cfg["dedupe"]
        corpus = self.store.corpus()
        rows = {p["slug"]: p for p in self.store.pages("passed")}
        offenders: dict[str, str] = {}
        for i, a in enumerate(corpus):
            for b in corpus[i + 1:]:
                j = jaccard(a["shingles"], b["shingles"])
                shared = len(sentences(a["body_text"]) & sentences(b["body_text"]))
                cos = float(np.dot(a["embedding"], b["embedding"])) if a["embedding"] is not None else 0
                faq_dups = []
                if a.get("faq_emb") is not None and b.get("faq_emb") is not None:
                    fs = a["faq_emb"] @ b["faq_emb"].T
                    faq_dups = [(a["faqs"][x], b["faqs"][y], float(fs[x, y]))
                                for x, y in zip(*np.where(fs >= d["faq_max_cosine"]))]
                if (j > d["page_max_jaccard"] or shared > d["max_shared_sentences"]
                        or cos > d["page_max_cosine"] or faq_dups):
                    a_newer = rows[a["slug"]]["updated_at"] >= rows[b["slug"]]["updated_at"]
                    newer, other = (a, b) if a_newer else (b, a)
                    why = (f"sweep: too similar to /{other['slug']} "
                           f"(jaccard {j:.3f}, shared sentences {shared}, cosine {cos:.3f})")
                    for qa_, qb_, s_ in faq_dups:
                        mine, theirs = (qa_, qb_) if a_newer else (qb_, qa_)
                        why += (f"; FAQ '{mine}' asks the same thing as /{other['slug']}'s '{theirs}' "
                                f"(cosine {s_:.3f}); replace it with a question specific to this page")
                    offenders[newer["slug"]] = why
        for slug, why in offenders.items():
            log.warning("%s %s", slug, why[:300])
            self.store.event(slug, "sweep", "warning", why)
            if final:
                self.store.upsert(slug, status="quarantined", error=why)
                self.stats["quarantined"] += 1
            else:
                self.store.upsert(slug, status="needs_revision", error=why)
        return offenders
