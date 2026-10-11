"""The three LLM roles: planner, writer (also reviser) and judge.

Each role is one structured-output call with a pydantic schema, so the model's answer is
validated JSON, never free text that has to be scraped.
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import create_model

from .llm import Provider
from .models import JudgeVerdict, KeywordRecord, PageBrief, PageContent, PlanBatch


class Agents:
    def __init__(self, provider: Provider, cfg, apps: dict, platform_facts: list[dict]):
        self.p = provider
        self.cfg = cfg
        self.apps = apps
        self.platform_facts = platform_facts
        self.env = Environment(loader=FileSystemLoader(str(cfg.path("prompts_dir"))),
                               undefined=StrictUndefined, keep_trailing_newline=True)

    def _system(self, app: dict, platform_fact_ids: list[str] | None = None) -> str:
        """platform_fact_ids=None -> all platform facts (planner). The writer and judge only get
        the facts the brief selected: tighter grounding, shorter prompts, and an exact dependency
        so that editing one fact regenerates only the pages that use it."""
        # trigger terms are for rule-based matching only; keep them out of the prompt
        clean = {k: app[k] for k in ("name", "platform", "pricing_summary")}
        clean["features"] = [{k: v for k, v in f.items() if k != "triggers"} for f in app["features"]]
        return self.env.get_template("system.md").render(
            app=app,
            app_facts_json=json.dumps(clean, indent=1),
            platform_facts_json=json.dumps(
                self.platform_facts if platform_fact_ids is None
                else [f for f in self.platform_facts if f["id"] in set(platform_fact_ids)], indent=1),
        )

    def _thinking(self, role: str) -> str | None:
        """Reasoning effort per role (config llm.thinking). 'low' is ~3x faster on Gemini 3.x
        Flash; first drafts, patches and the editor use it, while the planner and full rewrites
        (the cases where the model already failed once) keep the model's deeper default."""
        v = (self.cfg["llm"].get("thinking") or {}).get(role)
        return v or None

    def prompt_version(self) -> str:
        """Hash of all prompt templates: changing a prompt invalidates every page's input hash."""
        import hashlib

        h = hashlib.sha256()
        for p in sorted(Path(self.cfg.path("prompts_dir")).glob("*.md")):
            h.update(p.read_bytes())
        return h.hexdigest()[:12]

    # ---------------------------------------------------------------- planner
    def plan(self, batch: list[KeywordRecord], neighbour_slugs: dict[str, list[str]],
             keyword_of: dict[str, str], published: dict[str, PageBrief] | None = None) -> dict[str, PageBrief]:
        app = self.apps[batch[0].app_id]
        kws = [{
            "slug": r.slug, "keyword": r.keyword, "intent_guess": r.intent_guess,
            "platform": r.platform, "wants_free": r.wants_free,
            "source_format": r.source_format, "target_format": r.target_format,
            "candidate_features": r.candidate_features, "siblings": r.siblings,
            "also_covers": r.merged_keywords,
        } for r in batch]
        neigh = {r.slug: [{"slug": s, "keyword": keyword_of.get(s, s)} for s in neighbour_slugs.get(r.slug, [])]
                 for r in batch}
        # already-published neighbours keep their territory: the planner must route around them
        published = published or {}
        in_batch = {r.slug for r in batch}
        pub = {}
        for r in batch:
            for s in list(r.siblings) + neighbour_slugs.get(r.slug, []):
                if s in published and s not in in_batch:
                    b = published[s]
                    pub[s] = {"slug": s, "keyword": keyword_of.get(s, s), "unique_angle": b.unique_angle,
                              "answers": b.must_answer}
        prompt = self.env.get_template("plan.md").render(
            keywords_json=json.dumps(kws, indent=1), neighbours_json=json.dumps(neigh, indent=1),
            published_json=json.dumps(list(pub.values()), indent=1))
        out: PlanBatch = self.p.generate(
            task="plan", model=self.cfg["llm"]["planner_model"], system=self._system(app),
            prompt=prompt, schema=PlanBatch, temperature=self.cfg["llm"]["temperature_planner"],
            context={"records": batch, "neighbour_slugs": neighbour_slugs}, thinking=self._thinking("planner"),
        )
        briefs = {b.slug: b for b in out.briefs}
        valid_features = {f["id"] for f in app["features"]}
        valid_pf = {f["id"] for f in self.platform_facts}
        for r in batch:
            b = briefs.get(r.slug)
            if b is None:
                raise ValueError(f"planner returned no brief for {r.slug}")
            # sanitize: the planner may only reference things that exist
            b.feature_ids = list(dict.fromkeys(f for f in b.feature_ids if f in valid_features))[:5]
            for f in r.candidate_features + ["share_export", "privacy_ondevice", "auto_edge"]:
                if len(b.feature_ids) >= 3:
                    break
                if f in valid_features and f not in b.feature_ids:
                    b.feature_ids.append(f)
            b.platform_fact_ids = [f for f in b.platform_fact_ids if f in valid_pf]
            allowed_links = set(neighbour_slugs.get(r.slug, []))
            b.related_slugs = [s for s in b.related_slugs if s in allowed_links and s != r.slug][:4]
            b.exclusions = [e for e in b.exclusions if e.owner_slug != r.slug]
        return briefs

    # ---------------------------------------------------------------- writer / reviser
    def write(self, rec: KeywordRecord, brief: PageBrief, siblings: list[dict],
              feedback: list[str] | None = None, previous: PageContent | None = None) -> PageContent:
        app = self.apps[rec.app_id]
        prompt = self.env.get_template("write.md").render(
            record=rec, brief_json=brief.model_dump_json(indent=1), siblings=siblings,
            feedback=feedback or [], previous_json=previous.model_dump_json(indent=1) if previous else "",
        )
        return self.p.generate(
            task="revise" if feedback else "write", model=self.cfg["llm"]["writer_model"],
            system=self._system(app, brief.platform_fact_ids), prompt=prompt, schema=PageContent,
            temperature=self.cfg["llm"]["temperature_writer"],
            context={"record": rec, "brief": brief, "app": app},
            thinking=self._thinking("writer_revise" if feedback else "writer_draft"),
        )

    # ---------------------------------------------------------------- targeted patch
    def patch(self, rec: KeywordRecord, brief: PageBrief, content: PageContent, fields: list[str],
              feedback: list[str], siblings: list[dict]) -> PageContent:
        """Rewrite only the fields the QA errors point at (e.g. meta_description, faqs) and merge
        them back. Output is a few hundred tokens instead of a whole page, so a fix costs a
        fraction of a full rewrite and cannot break sections that already passed."""
        app = self.apps[rec.app_id]
        schema = create_model("PagePatch", **{f: (PageContent.model_fields[f].annotation, ...) for f in fields})
        prompt = self.env.get_template("patch.md").render(
            record=rec, brief_json=brief.model_dump_json(indent=1), fields=fields, feedback=feedback,
            siblings=siblings, page_json=content.model_dump_json(indent=1))
        part = self.p.generate(
            task="patch", model=self.cfg["llm"]["writer_model"], system=self._system(app, brief.platform_fact_ids),
            prompt=prompt, schema=schema, temperature=self.cfg["llm"]["temperature_writer"],
            context={"record": rec, "brief": brief, "app": app, "fields": fields},
            thinking=self._thinking("patch"),
        )
        return content.model_copy(update={f: getattr(part, f) for f in fields})

    # ---------------------------------------------------------------- judge
    def judge(self, rec: KeywordRecord, brief: PageBrief, content: PageContent,
              sibling_keywords: list[str]) -> JudgeVerdict:
        app = self.apps[rec.app_id]
        prompt = self.env.get_template("judge.md").render(
            record=rec, brief=brief, sibling_keywords=sibling_keywords,
            content_json=content.model_dump_json(indent=1))
        return self.p.generate(
            task="judge", model=self.cfg["llm"]["judge_model"], system=self._system(app, brief.platform_fact_ids),
            prompt=prompt, schema=JudgeVerdict, temperature=0.0, context={"record": rec},
            thinking=self._thinking("judge"),
        )
