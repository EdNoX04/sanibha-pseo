# Programmatic SEO agent: Document Scanner & PDF Toolkit

An agent pipeline that turns a keyword list into unique, publishable landing pages. It plans related pages together, writes each one, measures it against every other published page, and revises or quarantines anything too similar, generic or unsupported. It is built to run unattended on a schedule.

| Deliverable | Where |
|---|---|
| 1. Page template | [`docs/TEMPLATE_AND_SCHEMA.md` §1](docs/TEMPLATE_AND_SCHEMA.md) |
| 2. Data schema | [`docs/TEMPLATE_AND_SCHEMA.md` §2](docs/TEMPLATE_AND_SCHEMA.md), [`src/pseo/models.py`](src/pseo/models.py) |
| 3. Working pipeline | [`src/pseo/`](src/pseo/), tests in [`tests/`](tests/) |
| 4. The 12 generated pages | [`output/pages/`](output/pages/) as `.md` (readable on GitHub) and `.html` (open `index.html`), plus full structured data in [`output/data/`](output/data/) |
| Architecture diagrams | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (renders on GitHub) and [`docs/agent-flow.html`](docs/agent-flow.html): an interactive node diagram you open in a browser, with a replay of one real page's drafts |
| 5. README | this file. Evidence for every claim below is in [`output/report.md`](output/report.md) and [`output/similarity.csv`](output/similarity.csv) |

```bash
./run.sh explain convert-scanned-image-to-text   # the full story of one page: plan, every draft, every verdict
./run.sh keywords             # list keywords and each page's status
./run.sh add "scan receipts to Excel" --run        # add a keyword (and generate it now)
./run.sh replace "old keyword" "new keyword" --run # swap one; the old page is retired
./run.sh remove "scan receipts to Excel" --run     # remove one; its page is retired
./run.sh test                 # offline test suite (mock LLM, no key needed): 23 tests
./run.sh run                  # incremental: only new/changed keywords (prompts for GEMINI_API_KEY if unset)
./run.sh run --force          # regenerate every page
./run.sh render               # re-render HTML/MD + report from stored state, no LLM calls
./run.sh status               # recent runs and page states
```

If `./run.sh` reports "permission denied" after cloning, run `chmod +x run.sh` once (or call it as `bash run.sh ...`). `run.sh` creates a virtualenv and installs `requirements.txt` (Python 3.10+). The equivalent without it is `pip install -r requirements.txt && PYTHONPATH=src python -m pseo run`. The key can also go in `.env` (see `.env.example`, gitignored).

## Results from the real run

The published pages come from run #9, the current architecture: all 12 keywords exactly as given, written in parallel by `gemini-3.8-flash` (with `gemini-3.7-flash` as fallback when it was overloaded), checked by the code gates and by a separate editor model. No manual edits.

| | |
|---|---|
| Pages published / quarantined | **12 / 0** |
| Run time, all 12 pages | **232 s** (plan 49 s, write + check 182 s) |
| Generation calls | **28**: 1 plan, 12 drafts, 3 targeted patches, 12 editor reviews, **0 full rewrites** |
| Editor scores (specificity / usefulness / accuracy, 1–5) | **5/5/5 on every page**; every page passes the swap test |
| Highest 5-gram text overlap between any two pages | **0.015** Jaccard (mean 0.004; gate 0.12) |
| Verbatim shared sentences | **0** in the whole corpus |
| Highest semantic similarity between two pages | 0.930 cosine: *OCR handwriting iPhone* ↔ *convert scanned image to text*, the pair clustering flagged before writing (mean 0.85; gate 0.93) |
| Highest FAQ-question similarity across pages | 0.923 (gate 0.93) |
| Drafts rejected and fixed | 3, all by **patching one field**: a banned filler word ("seamless") in the feature list, and two FAQ questions that repeated a sibling page's |
| Length | 878–1,095 words per page |

### Before and after the architecture upgrade

The first version (v1) generated pages one at a time and answered every failure with a full rewrite. Run #9 is v2. The same 12 keywords, same models, same gates:

| | v1 (runs #1–#6) | v2 (run #9) |
|---|---|---|
| Time for 12 pages | ≈ 15 min (≈ 75 s per page, sequential; measured in runs #1 and #3) | **3.9 min** |
| Generation calls per page | ≈ 3 | **2.3** |
| Tokens (input / output) for 12 pages | ≈ 158k / 42k (run #3's per-page rate × 12) | **97k / 27k** (−39% / −36%) |
| Fix strategy | 9 full rewrites | 3 one-field patches, 0 rewrites |
| Editor scores | 4–5 | 5/5/5 everywhere |
| Max shared wording / max meaning similarity | 0.018 / 0.929 | 0.015 / 0.930 |

What changed, and why, is in [Speed and efficiency](#speed-and-efficiency). A simulated benchmark that isolates each change is in [`docs/BENCHMARK.md`](docs/BENCHMARK.md).

What the system handled on its own across runs (all logged in `output/report.md`):

1. The free tier's **daily quota ran out mid-run** (run #1). The circuit breaker stopped after 3 failures, still published what had passed, and the next run regenerated **only the 3 missing pages**. In run #9 the editor model's quota ran out again; that call fell back to the next model in the chain and the run carried on.
2. While reviewing output I found the Word page calling desktop Word "free". I corrected **one fact** in `data/platform_facts.yaml`, and the next run regenerated **only that one page**, because only its brief cited that fact.
3. When FAQ-level duplicate detection was added, the corpus sweep found two FAQ questions shared by the OCR and image-to-text pages and revised **only the newer page**.
4. In development the editor caught a template flaw: the *scan document without an app* page told readers to install the app (specificity 3, usefulness 2). The fix was to make the steps section follow search intent.

## How it works

Diagrams of the pipeline, the agent loop, the state and the scheduled run are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The text version:

```
data/keywords.csv  (keyword, app_id)
data/apps/*.yaml   app fact sheet: the only allowed source of claims about the app
data/platform_facts.yaml   iOS / third-party facts for the "other ways" section
        │
 1 INGEST    rules → KeywordRecord: slug, intent guess, formats, "free", candidate features
 2 CLUSTER   keyword embeddings → merge near-identical keywords (one URL), mark siblings,
             pick internal-link candidates
 3 PLAN      LLM, one call per sibling batch → PageBrief per keyword: search intent, unique
             angle, questions to answer, which page OWNS each shared subtopic, facts to use
 4 WRITE     LLM, structured JSON → PageContent in the fixed 10-section template, grounded only
             in the facts its brief selected; the prompt lists the siblings' published headings
             and FAQ questions so overlap is avoided up front. Up to 4 pages in parallel, never
             two siblings at once. Fast first draft (low reasoning effort).
 5 QA        rules: length, SEO fields, keyword coverage, banned filler, over-claims,
                    Pro-feature-called-free, generic headings
             duplicate gates vs an in-memory index of the WHOLE published corpus: 5-gram Jaccard,
                    verbatim sentences, page embedding cosine, FAQ-question cosine, titles/metas
             every error is tagged with the field it lives in
             LLM editor (a different model): specificity, usefulness, accuracy, swap test
               ├─ field-local errors  → 4b PATCH only those fields (small, fast call) → re-check
               ├─ page-level / editor → full REWRITE with the reasons (deeper reasoning) → re-check
               └─ still failing after 3 fixes → QUARANTINE: not published, alerted
 6 SWEEP     vectorised all-pairs check of the final corpus; the newer page of a clashing pair
             is revised (FAQ-only clash → patch)
 7 RENDER    HTML (canonical, HowTo + FAQPage JSON-LD), Markdown, JSON, index, sitemap
 8 REPORT    report.md + similarity.csv + run history; alerts, heartbeat, exit code
             state: SQLite (pages, input hashes, shingles, embeddings, runs, events)
```

This is an agent rather than a prompt in a loop because each draft is measured, and the specific failures go back to the model as instructions, routed by kind: a duplicated FAQ question gets a patch of the `faqs` field; *"semantically too close to /ocr-handwriting-iphone (cosine 0.935). That page already covers: … Rebuild the steps, tips and FAQs around this page's own angle: …"* gets a full rewrite. The loop runs until the page passes or is held back.

## Stack and why

| Choice | Why it fits this problem |
|---|---|
| **Python** | Mature LLM SDKs, text processing and numerics in one language, and an easy test harness. |
| **Gemini** (`gemini-3.8-flash` writer/planner, `gemini-3.5-flash` editor, `gemini-embedding-001`) | Structured output validated against pydantic schemas, embeddings from the same vendor, a free tier for development, and a per-request **reasoning-effort** control (`thinking_level`) that the pipeline sets per role. The editor is a different model from the writer, so it doesn't share the writer's blind spots, and because quotas are per model, the two don't compete for the same rate limit. Each role is a **fallback chain** in `config.yaml`: if a model is overloaded (503/504, timeout) or out of daily quota, the call moves to the next model. Only `llm.py` is vendor-specific. |
| **Pydantic schemas** | The page is typed data, not an HTML blob. Each section can be checked on its own, and malformed model output is retried, never rendered. |
| **Jinja2 templates** | Presentation is separate from content: a template change re-renders all pages with `pseo render` and no LLM calls. |
| **SQLite state** | A scheduled job needs memory: what's published, from which inputs, and the shingles and embeddings of every page for corpus-wide checks. Zero ops at this scale, and the `Store` interface is small enough to move to Postgres. |
| **GitHub Actions cron** | A free scheduler with secrets, logs, run history and failure emails. Committing state and pages back to git makes each content change a reviewable diff and each bad batch a `git revert`. |
| **No agent framework** | The control flow is a fixed loop (plan → write → measure → revise). Plain code is easier to test, debug and explain. |

## Avoiding thin and duplicate content

Duplicate content is the main risk, so it is handled at every stage rather than checked once at the end.

**1. Keyword stage (before any writing).** Keywords are embedded and compared. Near-identical keywords (cosine ≥ 0.96) are **merged** into one URL, with the second phrasing answered on that page: two pages for one intent cannibalise each other however differently they're worded. Close keywords become **siblings** (16 sibling pairs among the 12 keywords; see the report).

**2. Planning stage: who owns what.** Siblings are planned in one call, and each shared subtopic gets exactly one owner page. Other pages mention it in one sentence and link to it (`exclusions` in the brief). On later runs, already-published pages are passed to the planner as territory it must route around, so page 501 doesn't re-explain page 37.

**3. Writing stage: variation that comes from substance, not synonyms.**
* Each page gets its own scenario, angle and must-answer questions from the brief.
* Steps follow **intent**: the *without an app* page walks through the built-in Files/Notes scanner, and the *best free scanner* page is an evaluation checklist. The editor caught the original version of the template forcing app steps onto every page.
* The *alternatives* and *tips* sections are where tasks really differ (iOS has Markup for signing but nothing for PDF compression; thermal receipts fade; cursive defeats OCR).
* The writer only sees the facts its brief selected.
* **Prevention before detection:** the writer prompt lists every sibling's published section headings and FAQ questions, marked "already answered there, do not ask again in any wording". In v1, 3 of 8 rejections were a sibling overlap; in run #9, 9 of 12 first drafts passed every gate.

**4. Measured gates against the whole corpus**, not just the current run:

| Gate | Catches | Threshold |
|---|---|---|
| 5-word shingle Jaccard | same skeleton, keyword swapped | ≤ 0.12 |
| Verbatim shared sentences (≥ 8 words) | boilerplate leaking across pages | ≤ 1 |
| Page embedding cosine | paraphrased duplicates | ≤ 0.93 |
| FAQ question cosine + word overlap | two pages answering the same question | ≤ 0.93 / ≤ 0.5 |
| Title / meta near-duplicates | identical snippets in search results | token Jaccard < 0.8 / exact |
| LLM editor swap test | "would this page still read fine for a sibling keyword?" | must pass |

Thresholds were calibrated on real output. Across 1,705 cross-page FAQ pairs, the median similarity was 0.76 and the 99th percentile 0.88, and the pairs above 0.94 were real duplicates.

**5. Proof the gates work, not just that they exist** (`tests/`):
* `test_duplicate_drafts_are_caught_and_revised`: the mock LLM writes the same body for every page. QA rejects them all and the revise loop fixes them.
* `test_unfixable_page_is_quarantined_not_published`: the writer ignores feedback, so only one page is published and the run reports itself as degraded.
* `test_quality_gates_on_a_single_page`: over-claims, filler, Pro-as-free, generic headings and a duplicated FAQ are each detected.
* `test_field_local_errors_are_patched_not_rewritten`: a page whose only problem is a too-long meta description gets one patch of that field and no full rewrite.
* `test_parallel_scheduler_is_faster_and_never_runs_siblings_together`: with slow mock calls, 4 workers beat 1, and a spy on the scheduler confirms no two pages that share territory were ever in flight at the same time.

## Speed and efficiency

Profiling the v1 runs showed where the time went: pages were written one at a time (≈ 40 s per draft, most of it the model "thinking"), every failure triggered a full-page rewrite even when one FAQ question was the problem, and embedding calls queued behind generation calls in a single rate limiter. Six changes, each measured:

| Change | Why | Effect |
|---|---|---|
| **Sibling-aware parallel workers** (`pipeline._schedule`, `workers: 4`) | Pages are independent except for siblings, which must see each other's final text. A scheduler runs 4 pages at once but never two pages that share territory; the corpus sweep still catches anything that slips through. | ≈ 2.7× faster on its own (benchmark) |
| **Reasoning effort per role** (`llm.thinking`) | Measured on the same prompt: 3.1 s with `thinking_level: low` vs 9.1 s at the default. First drafts, patches and the editor run low (their output is checked anyway); planning and full rewrites, where quality is decided, keep the deeper default. | ≈ 1.8× faster on top; editor scores stayed 5/5/5 |
| **Targeted patches** (`agents.patch`, `prompts/patch.md`) | QA tags every error with the field it lives in. If all errors are field-local (an FAQ, the meta description, a banned word), only those fields are rewritten and merged back. | Run #9: 3 patches of ≈ 5 s, 0 full rewrites; v1 needed 9 rewrites |
| **Prevent instead of repair** (`write.md`) | The writer now sees each sibling's published section headings and FAQ questions. In v1, 3 of 8 rejections were overlap with a sibling. | Run #9: 9 of 12 first drafts passed every gate |
| **In-memory corpus index** (`index.py`) | v1 rebuilt the corpus from SQLite for every check. The index holds shingles, sentence sets, vectors and FAQ vectors once, updates on save, and answers meaning-similarity with one matrix product. Above 2,000 pages, candidates come from MinHash-LSH (now vectorised with numpy) plus nearest neighbours. | ≈ 6 ms per check at 12 pages; ≈ 250 ms at 500 pages, exact all-pairs |
| **Cheaper plumbing** | Per-model rate limiters (writer and editor quotas are separate), a separate embedding limiter, keyword embeddings cached in SQLite (an unchanged daily run makes no embedding call), and batches of keywords planned concurrently (≈ 45 planning calls at 500 keywords). | Removes queueing that did no work |

The architecture benchmark ([`docs/BENCHMARK.md`](docs/BENCHMARK.md), `scripts/benchmark.py`) replays the real pipeline against the offline mock with latencies measured from the live runs, so each change can be isolated without spending quota:

| Configuration (12 brief keywords) | Estimated time | Generation calls |
|---|---|---|
| v1: sequential, full rewrites, default reasoning | 17.5 min | 35 |
| + 4 sibling-aware workers | 6.5 min | 35 |
| + targeted patches | 6.2 min | 35 (8 rewrites become patches) |
| + reasoning effort per role (v2) | **3.5 min** | 35 |

The real run (#9) took 3.9 min, close to the benchmark's 3.5.

**Trade-off I accepted:** low-effort first drafts are about 11% shorter (958 words on average vs 1,073 in v1). They still sit well inside the 650–1,600 gate and every page scored 5/5/5 with the editor. Setting `writer_draft: ""` in `config.yaml` trades the speed back for length.

## Scaling to 500+ keywords without reading every page

* **Gates replace reviewers.** The same checks run on page 12 and page 5,000, and only passing pages publish. Humans look at the quarantine folder and the report's most-similar pairs, which is a handful of pages rather than 500.
* **Constant-size prompts.** The planner sees at most `plan_batch_size` sibling keywords plus each one's nearest published neighbours, never the whole corpus. Batches are planned concurrently.
* **Cheap duplicate checks.** Every check runs against the in-memory `CorpusIndex`, not the database. Below 2,000 pages every pair is compared exactly (≈ 250 ms per check at 500 pages). Above that, candidates come from vectorised MinHash-LSH (`similarity.py`, about 93% recall at Jaccard 0.2) plus each page's 25 nearest embedding neighbours, so a check costs the same at page 5,000 as at page 500.
* **Tested at 500.** The benchmark generates 500 synthetic keywords end to end through the real pipeline (mock LLM): **500 published, 0 quarantined, 1,348 generation calls** (45 plans, 500 drafts, 166 patches, 137 rewrites, 500 editor reviews), 0 sweep clashes. At the measured per-call latencies that is ≈ 36 min of API time with 8 workers, versus ≈ 4.9 h sequentially. Details in [`docs/BENCHMARK.md`](docs/BENCHMARK.md).
* **Exact incremental regeneration.** Each page stores a hash of its real inputs: keyword record, app fact sheet, **only the platform facts its brief uses**, prompt templates, and primary model names. Adding 50 keywords generates 50 pages, and editing one fact regenerates only the pages that use it (demonstrated above). Adding a fallback model regenerates nothing.
* **Multi-app.** `keywords.csv` has an `app_id` column and each app has its own fact sheet, so the whole portfolio runs through one pipeline.
* **Cost.** Run #9 measured about 8k input and 2.3k output tokens per page and 2.3 generation calls per page (plan share + draft + editor + ~0.25 patches) plus 1–2 embedding calls; the 500-keyword benchmark, with a pessimistic failure mix, needs 2.7. The free tier allows only about 20 requests per day per model, which is fine for development but not for 500 pages a day. Production needs a paid key; nothing else changes. `max_new_pages_per_run` caps any single run's spend.
* **Next step at volume: Gemini Batch Mode.** For a nightly job that doesn't need answers in seconds, batch requests cost 50% less (24 h turnaround target). I probed it with this key and the free tier rejects it (`400 FAILED_PRECONDITION`), so it is recommended rather than wired in; the draft and editor calls of a run would be the two batches.

## Running unattended

* **Schedule:** `.github/workflows/pseo.yml` runs the tests on every push and generation daily, plus a manual trigger with an optional `--force`. The API key, alert webhook and heartbeat URL are GitHub Secrets and are never stored in the repo.
* **Safe to re-run and resume:** runs are incremental by input hash. An exclusive file lock (released by the OS if the process dies) plus Actions `concurrency` mean runs never overlap. A killed run is marked `aborted` at the next start.
* **Resilient calls:** a client-side rate limiter per model (writer and editor quotas are separate) plus one for embeddings, a 180 s hard timeout per request, retries with backoff that honour the server's `retryDelay`, and model fallback on overload or daily quota.
* **Contained failures:** a failing page is recorded as `failed` and the run continues. Three consecutive failures (revoked key, all quota gone) trip a **circuit breaker**: generation stops, but pages that passed are still published and the report is still written. The breaker fired for real in run #1 when the daily quota ran out (`tests/test_circuit_breaker_stops_but_still_publishes` covers it).
* **Would we know if it broke?** Three failure modes, three signals:
  1. **Crash or abort:** exit code 1, webhook alert (Slack/Discord) with the reason, heartbeat `/fail`, failed Actions job (GitHub emails the owner).
  2. **"Succeeded" but output is bad:** more than 25% of attempted pages quarantined or failed marks the run **degraded**: exit code 2, plus an alert listing each page and why.
  3. **Never ran at all** (disabled workflow, expired secret): nothing inside the job can report that, so it pings a dead-man's-switch heartbeat (healthchecks.io-style), which alerts when the expected ping doesn't arrive.
* **Audit trail:** every run, QA rejection and incident is in SQLite (`runs`, `events`) and in `output/report.md`, which is also posted to the Actions job summary.
* **Removed keywords** are marked `retired` and dropped from output and the sitemap (a production site would 301 them).

## Honest limitations

* **The app fact sheet is assumed.** `data/apps/doc-scanner.yaml` was written from the brief's one-line description. Its features, and which ones are Pro, are placeholders. Pages only claim what's in that file, so dropping in Sanibha's real sheet and re-running regenerates every page.
* **Competitors aren't covered.** The *best free PDF scanner app* page compares the app with built-in iOS tools and generic categories, not named competitor apps. The pipeline has no verified competitor facts and is told not to invent them. A vetted competitor fact file would slot in like `platform_facts.yaml`.
* **The editor is an LLM.** It caught real problems (invented UI, filler, intent mismatch) but can be wrong in either direction. That's why it is one gate among several measurable ones, not the only one.
* **Thresholds were calibrated on 12 pages.** The closest pair sits at 0.930 against a 0.93 gate: these two keywords overlap in meaning, and the pages are differentiated (handwriting vs printed text) but close. At 500 pages, the report's similarity distribution would be used to re-tune thresholds, and keyword pairs like this would be candidates for merging.
* **No keyword research inputs.** Search volume, difficulty and SERP analysis would be the next input; SERP data would let the planner target gaps in what currently ranks.
* **The speed benchmark is a simulation.** It replays the real pipeline against the mock with latencies measured from live runs; the real run #9 (3.9 min) landed close to its 3.5 min estimate, but the 500-keyword figures are estimates, not a live 500-page run, which the free tier can't support.
* **Low-effort drafts are shorter** (≈ 11%, see above), a deliberate speed/length trade.
* **Prompt caching is not exploited.** Gemini's implicit cache needs a shared prompt prefix of at least 4,096 tokens on current Flash models. The writer's system prompt is kept short on purpose (only the facts its brief cites, which also makes regeneration exact), so it sits below that. At paid volume an explicit cache of the full fact sheet is an option; it trades against fact-scoped prompts.
* **Publishing to a CMS, Search Console and indexing are out of scope** per the brief. The output (static HTML/Markdown/JSON plus a sitemap) is what a CMS importer would consume.

## Research behind the design

The v2 changes came from profiling the v1 runs and from these sources:

* **Google Search, [spam policies: scaled content abuse](https://developers.google.com/search/docs/essentials/spam-policies#scaled-content)** and **[creating helpful, reliable, people-first content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content).** Generating many pages is not itself the violation; generating many pages that add no value is. This is why every page has its own brief, scenario and grounded facts, why the editor runs a swap test, and why failing pages are quarantined rather than published.
* **Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).** The pipeline is a fixed workflow rather than an autonomous agent, using three of the patterns described there: *parallelization* (independent pages run concurrently), *routing* (field-local failures go to a patch, page-level ones to a rewrite) and *evaluator-optimizer* (measured gates and an editor feed specific failures back to the writer).
* **Gemini API docs: [thinking](https://ai.google.dev/gemini-api/docs/thinking), [structured output](https://ai.google.dev/gemini-api/docs/structured-output), [Batch Mode](https://ai.google.dev/gemini-api/docs/batch-mode), [context caching](https://ai.google.dev/gemini-api/docs/caching).** Basis for per-role `thinking_level`, schema-validated output and dynamic patch schemas, the Batch Mode recommendation, and the caching trade-off above.
* **Leskovec, Rajaraman and Ullman, [*Mining of Massive Datasets*](http://www.mmds.org/), ch. 3.** MinHash and banded LSH for finding near-duplicate documents without comparing every pair.

## Repository layout

```
config.yaml                  models, thresholds, rate limits (env override: PSEO_<SECTION>_<KEY>)
run.sh                       one-command runner
data/keywords.csv            input keywords (+ app_id)
data/apps/doc-scanner.yaml   app fact sheet (grounding)
data/platform_facts.yaml     iOS / third-party facts for the alternatives section
prompts/                     system, plan, write (+ revision), patch, editor prompts
templates/                   page.html.j2, page.md.j2
src/pseo/
  keywords.py   1  rules → KeywordRecord
  cluster.py    2  merge / siblings / link candidates
  agents.py   3-5  planner, writer/reviser, field patcher, editor
  quality.py    5  QA gates
  similarity.py    shingles, sentences, vectorised MinHash-LSH, cosine
  index.py         in-memory corpus index used by every duplicate check
  pipeline.py      orchestration, sibling-aware parallel scheduler, patch/rewrite loop, sweep,
                   circuit breaker, incremental hashing
  render.py     7  HTML / MD / JSON / index / sitemap
  report.py     8  report.md, similarity.csv
  store.py         SQLite state
  ops.py           run lock, alerts, heartbeat
  llm.py           Gemini provider (rate limit, timeouts, retries, fallback) + offline mock
  cli.py           python -m pseo run | render | status | explain | keywords | add | remove | replace
  keyword_admin.py safe edits to data/keywords.csv (dedupe, app check, atomic write)
  explain.py       per-page trace: inputs, brief, every draft and verdict
tests/             offline end-to-end and unit tests
scripts/benchmark.py  architecture benchmark (mock LLM, measured latencies)
.github/workflows/ schedule + CI
output/            generated pages, report, manifest, sitemap, run history
state/pseo.sqlite  pipeline memory
```
