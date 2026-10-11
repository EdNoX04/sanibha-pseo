"""`pseo explain <slug>`: print how one page was made, stage by stage, from the stored state.

Nothing here calls an LLM; it reads what the pipeline recorded, so it is also the quickest way
to see the agent loop (rejected drafts and the reasons) without re-running anything.
"""
from __future__ import annotations

import json
import textwrap
import time

from .keywords import slugify
from .report import final_neighbours


def _wrap(text: str, indent: int = 6, width: int = 100) -> str:
    return textwrap.fill(text, width=width, initial_indent=" " * indent, subsequent_indent=" " * indent)


def explain(store, page: str | None) -> str:
    rows = {r["slug"]: r for r in store.pages()}
    if not page:
        lines = ["Pages (run `./run.sh explain <slug>` for the full story of one):", ""]
        for s, r in rows.items():
            lines.append(f"  {r['status']:<12} {s:<40} \"{r['keyword']}\"")
        return "\n".join(lines)
    slug = page if page in rows else slugify(page)
    r = rows.get(slug)
    if not r:
        return f"No page '{page}'. Run `./run.sh explain` to list pages."

    rec = json.loads(r["record_json"] or "{}")
    brief = json.loads(r["brief_json"] or "{}")
    content = json.loads(r["content_json"] or "{}")
    qa = json.loads(r["qa_json"] or "{}")
    judge = json.loads(r["judge_json"] or "null")
    L = [f"\"{r['keyword']}\"  ->  /{slug}   [{r['status'].upper()}]", ""]

    L.append("1 INGEST  (rules, no AI)")
    L.append(f"    intent guess: {rec.get('intent_guess')}   platform: {rec.get('platform')}   "
             f"wants free: {rec.get('wants_free')}   formats: {rec.get('source_format')} -> {rec.get('target_format')}")
    L.append(f"    app features whose trigger words appear in the keyword: {', '.join(rec.get('candidate_features', [])) or '-'}")
    L.append("")
    L.append("2 CLUSTER  (keyword embeddings)")
    L.append(f"    close keywords that must be kept apart: {', '.join(rec.get('siblings', [])) or '-'}")
    L.append("")
    L.append("3 PLAN  (planner AI, all related keywords in one call)")
    for k, label in (("intent_type", "intent"), ("searcher_goal", "searcher goal"),
                     ("scenario", "scenario"), ("unique_angle", "unique angle")):
        L.append(f"    {label}:")
        L.append(_wrap(str(brief.get(k, "")), 8))
    if brief.get("ambiguity_note"):
        L.append("    ambiguity:")
        L.append(_wrap(brief["ambiguity_note"], 8))
    L.append("    questions it must answer:")
    L += [_wrap("- " + q, 8) for q in brief.get("must_answer", [])]
    if brief.get("exclusions"):
        L.append("    topics left to other pages (mentioned once + linked):")
        L += [_wrap(f"- {e['topic']}  ->  /{e['owner_slug']}", 8) for e in brief["exclusions"]]
    L.append(f"    app features to use: {', '.join(brief.get('feature_ids', []))}")
    L.append(f"    platform facts it may cite: {', '.join(brief.get('platform_fact_ids', []))}")
    L.append("")

    L.append("4-5 WRITE -> CHECK -> REVISE  (every draft, all runs)")
    runs = {x["id"]: x for x in store.recent_runs(1000)}
    ev = store.db.execute("SELECT run_id, ts, stage, level, message FROM events WHERE slug=? ORDER BY ts",
                          (slug,)).fetchall()
    if not ev:
        L.append("    (no events recorded)")
    for e in ev:
        when = time.strftime("%H:%M", time.gmtime(e["ts"]))
        msg = e["message"]
        if "Traceback" in msg:
            msg = [x for x in msg.strip().splitlines() if x.strip()][-1]
        verdict = "PASSED" if "PASS" in msg and e["level"] == "info" else (
            "FAILED (error)" if e["level"] == "error" else "REJECTED")
        if e["stage"] == "sweep":
            verdict = "FLAGGED by corpus sweep"
        msg = msg.split(": ", 1)[1] if msg.startswith("attempt") and ": " in msg else msg
        L.append(f"    run #{e['run_id']} {when} UTC  {verdict}")
        if verdict != "PASSED":
            L.append(_wrap(msg[:900], 8))
    L.append("")

    if r["status"] == "passed":
        nb = final_neighbours(store.corpus()).get(slug, {})
        m = qa.get("metrics", {})
        L.append("FINAL PAGE")
        L.append(f"    title: {content.get('title')}")
        L.append(f"    words: {m.get('words')}   writer: {m.get('writer_model', '-')}   editor: {m.get('judge_model', '-')}")
        L.append(f"    closest other page by meaning: /{nb.get('max_cosine_with')} (cosine {nb.get('max_cosine')}, limit 0.93)")
        L.append(f"    most shared wording: /{nb.get('max_jaccard_with')} (5-word overlap {nb.get('max_jaccard')}, limit 0.12)")
        if judge:
            L.append(f"    editor scores: specificity {judge['specificity']}/5, usefulness {judge['usefulness']}/5, "
                     f"accuracy {judge['accuracy_risk']}/5, swap test {judge['swap_test']}")
        L.append(f"    files: output/pages/{slug}.html  .md   output/data/{slug}.json")
    return "\n".join(L)
