"""Stage 6 - render published pages to HTML + Markdown + JSON, plus index, manifest, sitemap.

Rendering is a pure function of what is in the store, so it is re-run for the whole corpus at
the end of every run. That keeps internal links correct: a page only links to pages that are
actually published right now.
"""
from __future__ import annotations

import html
import json
import shutil
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import KeywordRecord, PageBrief, PageContent
from .report import final_neighbours


def _jsonld(c: PageContent, url: str, app: dict, intent: str) -> str:
    howto = {
        "@type": "HowTo",
        "name": c.steps_heading,
        "description": c.quick_answer,
        "step": [{"@type": "HowToStep", "position": i + 1, "name": s.title, "text": s.detail}
                 for i, s in enumerate(c.steps)],
    }
    if intent not in ("no_app_alternative", "comparison"):  # steps use the app only for these intents
        howto["tool"] = {"@type": "SoftwareApplication", "name": app["name"], "operatingSystem": "iOS"}
    graph = [
        howto,
        {
            "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": f.question,
                            "acceptedAnswer": {"@type": "Answer", "text": f.answer}} for f in c.faqs],
        },
    ]
    data = {"@context": "https://schema.org", "@graph": graph, "url": url}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _iso(ts: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""


def render_all(store, cfg, apps: dict, run_info: dict | None = None) -> dict:
    """run_info: {"run_id", "started_at", "stats"} of the run doing the rendering (None when
    re-rendering from state), shown as a banner on the index so every run is visible."""
    out_dir = cfg.path("output_dir")
    pages_dir = out_dir / "pages"
    data_dir = out_dir / "data"
    quarantine_dir = out_dir / "quarantine"
    for d in (pages_dir, data_dir, quarantine_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    env = Environment(loader=FileSystemLoader(str(cfg.path("templates_dir"))),
                      autoescape=select_autoescape(["html", "j2"]))
    md_env = Environment(loader=FileSystemLoader(str(cfg.path("templates_dir"))), autoescape=False)
    md_env.filters["cell"] = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    base_url = cfg["site"]["base_url"].rstrip("/")
    published = store.pages("passed")
    titles = {p["slug"]: json.loads(p["content_json"])["title"] for p in published}
    fin = final_neighbours(store.corpus())
    manifest = []

    for p in published:
        rec = KeywordRecord.model_validate_json(p["record_json"])
        brief = PageBrief.model_validate_json(p["brief_json"])
        c = PageContent.model_validate_json(p["content_json"])
        app = apps[rec.app_id]
        features = {f["id"]: f for f in app["features"]}
        link_slugs = list(dict.fromkeys(brief.related_slugs + [e.owner_slug for e in brief.exclusions]))
        related = [{"slug": s, "title": titles[s]} for s in link_slugs if s in titles and s != rec.slug][:5]
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p["updated_at"]))
        ctx = dict(c=c, rec=rec, brief=brief, app=app, features=features, related=related,
                   slug=rec.slug, base_url=base_url, generated_at=generated_at,
                   jsonld=_jsonld(c, f"{base_url}/{rec.slug}", app, brief.intent_type))
        (pages_dir / f"{rec.slug}.html").write_text(env.get_template("page.html.j2").render(**ctx))
        (pages_dir / f"{rec.slug}.md").write_text(md_env.get_template("page.md.j2").render(**ctx))
        qa = json.loads(p["qa_json"] or "{}")
        judge = json.loads(p["judge_json"] or "null")
        (data_dir / f"{rec.slug}.json").write_text(json.dumps({
            "record": rec.model_dump(), "brief": brief.model_dump(), "content": c.model_dump(),
            "qa": qa, "judge": judge, "attempts": p["attempts"], "input_hash": p["input_hash"],
        }, indent=2, ensure_ascii=False))
        manifest.append({"slug": rec.slug, "keyword": rec.keyword, "title": c.title, "intent": brief.intent_type,
                         "url": f"{base_url}/{rec.slug}", "words": qa.get("metrics", {}).get("words"),
                         "max_jaccard": fin.get(rec.slug, {}).get("max_jaccard"),
                         "max_cosine": fin.get(rec.slug, {}).get("max_cosine"),
                         "closest_page": fin.get(rec.slug, {}).get("max_cosine_with"),
                         "judge": judge, "attempts": p["attempts"], "related": [r["slug"] for r in related],
                         "content_changed_at": _iso(p["updated_at"]),
                         "changed_this_run": bool(run_info and p["updated_at"]
                                                  and p["updated_at"] >= run_info["started_at"])})

    for p in store.pages("quarantined"):
        (quarantine_dir / f"{p['slug']}.json").write_text(json.dumps({
            "keyword": p["keyword"], "error": p["error"], "qa": json.loads(p["qa_json"] or "{}"),
            "content": json.loads(p["content_json"] or "null"),
        }, indent=2, ensure_ascii=False))

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for m in manifest:
        sitemap.append(f"  <url><loc>{html.escape(m['url'])}</loc></url>")
    sitemap.append("</urlset>")
    (out_dir / "sitemap.xml").write_text("\n".join(sitemap) + "\n")

    if run_info:
        st = run_info["stats"]
        rebuilt = sum(m["changed_this_run"] for m in manifest)
        banner = (f"<div class=\"banner\"><b>Run #{run_info['run_id']}</b> · <time datetime=\"{_iso(run_info['started_at'])}\">"
                  f"{_iso(run_info['started_at'])}</time><br>{rebuilt} page(s) written or rewritten this run · "
                  f"{st.get('skipped_unchanged', 0)} unchanged (inputs identical, so not regenerated) · "
                  f"{st.get('revisions', 0)} revision(s) · {st.get('quarantined', 0)} quarantined · "
                  f"{st.get('failed', 0)} failed · {st.get('merged', 0)} merged</div>")
    else:
        banner = "<div class=\"banner\">Re-rendered from stored state (no generation run).</div>"
    new_cls, new_pill = ' class="new"', ' <span class="pill">new this run</span>'
    rows = "\n".join(
        f"<tr{new_cls if m['changed_this_run'] else ''}><td><a href=\"{m['slug']}.html\">{html.escape(m['title'])}</a>"
        f"{new_pill if m['changed_this_run'] else ''}</td>"
        f"<td>{html.escape(m['keyword'])}</td><td>{m['intent']}</td><td>{m['words']}</td>"
        f"<td>{m['max_jaccard']}</td><td>{m['max_cosine']}</td><td>{m['attempts']}</td>"
        f"<td><time datetime=\"{m['content_changed_at']}\">{m['content_changed_at']}</time></td></tr>"
        for m in manifest)
    (pages_dir / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Generated guides</title>
<style>body{{font-family:system-ui,sans-serif;max-width:72rem;margin:2rem auto;padding:0 1rem;line-height:1.5}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:.4rem;text-align:left;vertical-align:top}}
.banner{{background:#eef4fc;border:1px solid #c9dcf5;border-radius:8px;padding:.75rem 1rem;margin:1rem 0}}
tr.new td{{background:#f1faf4}}.pill{{font-size:.75rem;background:#1d7f47;color:#fff;border-radius:999px;padding:1px 8px;white-space:nowrap}}
.wrap{{overflow-x:auto}}</style></head>
<body><h1>Generated guides ({len(manifest)})</h1>
{banner}
<p>Every page below was generated, checked and revised by the pipeline without manual edits. A page is only
regenerated when something it depends on changes (its keyword, the app facts, the facts it cites, the prompts or the
model), so on a normal run most rows stay the same: that is the incremental design, not a stale page.
Max Jaccard / max cosine = the page's highest wording overlap / meaning similarity with any other published page
(lower is more unique). Drafts = attempts in its last generation. Run <code>./run.sh explain &lt;slug&gt;</code>
for a page's full history, or open <code>output/report.md</code> for every pair.</p>
<div class="wrap"><table><thead><tr><th>Page</th><th>Keyword</th><th>Intent</th><th>Words</th><th>Max Jaccard</th><th>Max cosine</th><th>Drafts</th><th>Content last changed</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<script>document.querySelectorAll("time").forEach(t=>{{const d=new Date(t.dateTime);if(!isNaN(d))t.textContent=d.toLocaleString();}});</script>
</body></html>""")
    return {"published": len(manifest), "quarantined": len(store.pages("quarantined"))}
