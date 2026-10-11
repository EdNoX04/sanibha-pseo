"""Run report: the evidence for "are these pages actually different?" and "did the run work?".

Writes output/report.md (human-readable) and output/similarity.csv (every pair of pages).
"""
from __future__ import annotations

import csv
import json
import time

import numpy as np

from .similarity import jaccard, sentences


def _matrix(corpus):
    n = len(corpus)
    J = np.zeros((n, n))
    C = np.zeros((n, n))
    S = np.zeros((n, n), dtype=int)
    sents = [sentences(c["body_text"]) for c in corpus]
    for i in range(n):
        for k in range(n):
            if i == k:
                continue
            J[i, k] = jaccard(corpus[i]["shingles"], corpus[k]["shingles"])
            if corpus[i]["embedding"] is not None and corpus[k]["embedding"] is not None:
                C[i, k] = float(np.dot(corpus[i]["embedding"], corpus[k]["embedding"]))
            S[i, k] = len(sents[i] & sents[k])
    return J, C, S


def final_neighbours(corpus) -> dict[str, dict]:
    """Each published page's closest neighbour in the FINAL corpus (QA-time metrics only saw
    the pages that existed when the page was written)."""
    if len(corpus) < 2:
        return {c["slug"]: {} for c in corpus}
    J, C, S = _matrix(corpus)
    out = {}
    for i, c in enumerate(corpus):
        j, k = int(np.argmax(J[i])), int(np.argmax(C[i]))
        out[c["slug"]] = {"max_jaccard": round(float(J[i, j]), 4), "max_jaccard_with": corpus[j]["slug"],
                          "max_cosine": round(float(C[i, k]), 4), "max_cosine_with": corpus[k]["slug"],
                          "shared_sentences": int(S[i].sum())}
    return out


def write_report(store, cfg, run_id: int, result: dict, stats: dict) -> None:
    out = cfg.path("output_dir")
    out.mkdir(parents=True, exist_ok=True)
    corpus = store.corpus()
    slugs = [c["slug"] for c in corpus]
    J, C, S = _matrix(corpus) if corpus else (np.zeros((0, 0)),) * 3

    with (out / "similarity.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["page_a", "page_b", "shingle_jaccard", "embedding_cosine", "shared_sentences"])
        for i in range(len(slugs)):
            for k in range(i + 1, len(slugs)):
                w.writerow([slugs[i], slugs[k], f"{J[i, k]:.4f}", f"{C[i, k]:.4f}", int(S[i, k])])

    d = cfg["dedupe"]
    L = []
    L.append(f"# pSEO run report: run #{run_id}")
    L.append("")
    L.append(f"- **Status:** {result['status']}")
    L.append(f"- **Finished:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    L.append(f"- **Duration:** {result.get('duration_s', '?')} s")
    L.append(f"- **Provider:** {cfg['llm']['provider']} (writer `{cfg['llm']['writer_model']}`, "
             f"embeddings `{cfg['llm']['embed_model']}`)")
    u = result.get("usage", {})
    L.append(f"- **LLM usage:** {u.get('calls', 0)} generate calls {u.get('by_task', {})}, "
             f"{u.get('embed_calls', 0)} embed calls, {u.get('input_tokens', 0):,} input / "
             f"{u.get('output_tokens', 0):,} output tokens, {u.get('retries', 0)} retries")
    L.append(f"- **Counts:** " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    L.append(f"- **Published pages in corpus:** {len(corpus)}")
    L.append("")
    if result.get("problems"):
        L.append("## Problems")
        L += [f"- {p}" for p in result["problems"]]
        L.append("")

    cl = result.get("cluster") or {}
    L.append("## Keyword clustering (cannibalisation check before writing)")
    L.append("")
    if cl.get("merged"):
        L += [f"- MERGED `{a}` into /{b} (cosine {c})" for a, b, c in cl["merged"]]
    else:
        L.append(f"- No keyword pair above the merge threshold ({d['keyword_merge_cosine']}): every keyword gets its own page.")
    if cl.get("sibling_pairs"):
        L.append("- Sibling pairs planned together and explicitly differentiated:")
        L += [f"  - /{a} ↔ /{b} (cosine {c})" for a, b, c in cl["sibling_pairs"]]
    L.append("")

    if len(corpus) >= 2:
        offdiag = ~np.eye(len(slugs), dtype=bool)
        L.append("## Duplicate-content metrics across all published pages")
        L.append("")
        L.append(f"| Metric | Max pair | Mean | Threshold |")
        L.append("|---|---|---|---|")
        L.append(f"| 5-word shingle Jaccard | {J[offdiag].max():.4f} | {J[offdiag].mean():.4f} | {d['page_max_jaccard']} |")
        L.append(f"| Embedding cosine | {C[offdiag].max():.4f} | {C[offdiag].mean():.4f} | {d['page_max_cosine']} |")
        L.append(f"| Verbatim shared sentences (≥8 words) | {S[offdiag].max()} | {S[offdiag].mean():.2f} | {d['max_shared_sentences']} |")
        fq = []
        for i, a in enumerate(corpus):
            for b in corpus[i + 1:]:
                if a.get("faq_emb") is not None and b.get("faq_emb") is not None:
                    fq.extend((a["faq_emb"] @ b["faq_emb"].T).ravel().tolist())
        if fq:
            L.append(f"| FAQ question cosine (across pages) | {max(fq):.4f} | {np.mean(fq):.4f} | {d['faq_max_cosine']} |")
        L.append("")
        L.append("Most similar pairs:")
        L.append("")
        pairs = sorted(((C[i, k], J[i, k], S[i, k], slugs[i], slugs[k]) for i in range(len(slugs))
                        for k in range(i + 1, len(slugs))), reverse=True)[:8]
        L.append("| Page A | Page B | Cosine | Jaccard | Shared sentences |")
        L.append("|---|---|---|---|---|")
        L += [f"| {a} | {b} | {c:.4f} | {j:.4f} | {s} |" for c, j, s, a, b in pairs]
        L.append("")

    L.append("## Pages")
    L.append("")
    L.append("Closest neighbour = the most similar other published page in the final corpus.")
    L.append("")
    L.append("| Page | Status | Words | Drafts | Editor (spec/use/acc/swap) | Max Jaccard | Max cosine (closest page) |")
    L.append("|---|---|---|---|---|---|---|")
    fin = final_neighbours(corpus)
    for p in store.pages():
        qa = json.loads(p["qa_json"] or "{}")
        m = {**qa.get("metrics", {}), **fin.get(p["slug"], {})}
        jd = json.loads(p["judge_json"]) if p["judge_json"] else None
        js = f"{jd['specificity']}/{jd['usefulness']}/{jd['accuracy_risk']}/{jd['swap_test']}" if jd else "-"
        L.append(f"| {p['slug']} | {p['status']} | {m.get('words', '-')} | {p['attempts']} | {js} | "
                 f"{m.get('max_jaccard', '-')} | {m.get('max_cosine', '-')} ({m.get('max_cosine_with') or '-'}) |")
    L.append("")

    # agent-loop evidence across ALL runs: what QA rejected and the writer then fixed
    runs = {r["id"]: r for r in store.recent_runs(1000)}
    ev = store.db.execute(
        "SELECT run_id, slug, stage, level, message FROM events WHERE level != 'info' ORDER BY ts").fetchall()
    if ev:
        L.append("## Rejections, revisions and incidents (all runs)")
        L.append("")
        L.append("Each QA line is a draft that was rejected; its errors were sent back to the writer as revision "
                 "instructions, and the page's later attempt is the one that was published. Run-level lines are "
                 "incidents the pipeline handled itself.")
        last = None
        for r in ev:
            if r["run_id"] != last:
                last = r["run_id"]
                info = runs.get(last, {})
                started = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(info["started_at"])) if info else "?"
                L.append("")
                L.append(f"**Run #{last}** ({info.get('status', '?')}, started {started})")
                L.append("")
            msg = r["message"]
            if "Traceback (most recent call last)" in msg:   # keep the report readable
                msg = [ln for ln in msg.strip().splitlines() if ln.strip()][-1]
            L.append(f"- **{r['slug'] or 'run'}** [{r['stage']}/{r['level']}]: {msg[:700]}")
        L.append("")

    text = "\n".join(L) + "\n"
    (out / "report.md").write_text(text)
    hist = out / "runs"
    hist.mkdir(exist_ok=True)
    (hist / f"run-{run_id:04d}.md").write_text(text)
