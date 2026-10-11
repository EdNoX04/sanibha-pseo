# Architecture benchmark

`scripts/benchmark.py` measures what each v2 change buys without spending API quota. It runs the **real pipeline** (ingest, clustering, the parallel scheduler, every QA gate, the corpus sweep, rendering) against the offline mock LLM, with call latencies taken from the live Gemini runs:

| Call | Simulated latency | Source |
|---|---|---|
| Plan (one per sibling batch) | ≈ 60 s | live runs |
| Draft / full rewrite, default reasoning | ≈ 30 s | live runs (≈ 40 s per draft including embedding + editor) |
| Same call with `thinking_level: low` | × 0.35 | measured 3.1 s vs 9.1 s on the same prompt |
| Patch of one or two fields | ≈ 6 s | run #9 |
| Editor review | ≈ 8 s | live runs |
| Embedding | ≈ 0.5 s | live runs |

A first-draft failure mix like the real runs is injected: about a third of first drafts fail a field-local check (meta description too long) and about a quarter a page-level one (too close to a sibling). Latencies are divided by 100 so the benchmark finishes quickly, and reported back × 100.

```bash
PYTHONPATH=src python scripts/benchmark.py               # 12 brief keywords, four configurations
PYTHONPATH=src python scripts/benchmark.py --scale-test  # + 500 synthetic keywords
```

## 12 brief keywords: isolating each change

| Configuration | Estimated time | Generation calls | Full rewrites | Patches | Published |
|---|---|---|---|---|---|
| v1: sequential, full rewrites, default reasoning | 17.5 min | 35 | 10 | 0 | 12 |
| + 4 sibling-aware workers | 6.5 min | 35 | 10 | 0 | 12 |
| + targeted patches | 6.2 min | 35 | 2 | 8 | 12 |
| + reasoning effort per role (**v2**) | **3.5 min** | 35 | 2 | 8 | 12 |

The call count stays at 35 in every row because the injected failure mix is the same; what changes is how long each call takes and how many run at once. Patches matter more for tokens than for time here: a patch outputs a few hundred tokens instead of a whole page.

**Check against reality:** the live run #9 with the v2 configuration took **3.9 min** (232 s) for the 12 pages, with 28 generation calls (fewer than the benchmark's 35 because 9 of 12 real first drafts passed every gate).

## 500 synthetic keywords: does it scale?

500 distinct keywords (20 task verbs × 25 document types, e.g. *"password protect a lease agreement"*), 8 workers, v2 configuration:

| | Result |
|---|---|
| Published / quarantined | **500 / 0** |
| Generation calls | **1,348** (45 plans, 500 drafts, 166 patches, 137 rewrites, 500 editor reviews), 2.7 per page |
| Embedding calls | 804 (keyword vectors are cached; page + FAQ vectors in one call per draft) |
| Corpus sweep clashes | 0 |
| Duplicate-check cost | ≈ 250 ms per check at 500 pages, comparing exactly against every page |
| API time at measured latencies | **≈ 36 min with 8 workers**, ≈ 4.9 h sequentially |

An earlier 500-keyword pass compared v1 and v2 head to head (its synthetic keywords still had some near-duplicates, so clustering merged them into 475 pages): v1 needed 291 full rewrites where v2 needed 131 rewrites plus 160 patches, and v1's estimated time was about 4× longer (807 vs 203 simulated minutes).

## What the benchmark does not show

* **It is a simulation.** Latencies are measured, but the mock's text is not real writing, so content quality comes only from the live runs (see `output/report.md`).
* **Meaning-based gates are switched off in the 500 run.** The mock's embeddings are bag-of-words, so 500 template pages look ≈ 0.93 alike to them, which would quarantine pages for a reason that does not exist with real embeddings. The wording gates (shingles, shared sentences, titles, metas) stay on. The meaning gates are exercised by the live Gemini runs.
* **Rate limits decide real throughput.** "36 min with 8 workers" assumes a paid key whose per-minute limits allow 8 concurrent calls. The free tier (≈ 20 requests per model per day) cannot run 500 pages in a day at any speed.
* **Wall-clock of the simulated run includes CPU time scaled × 100**, so its raw total overstates; the API-time figure is computed from the call log instead.

Raw outputs: the script prints a JSON summary; the runs above produced the numbers in this file.
