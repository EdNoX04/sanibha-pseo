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
./run.sh test                 # offline test suite (mock LLM, no key needed): 21 tests
./run.sh run                  # incremental: only new/changed keywords (prompts for GEMINI_API_KEY if unset)
./run.sh run --force          # regenerate every page
./run.sh render               # re-render HTML/MD + report from stored state, no LLM calls
./run.sh status               # recent runs and page states
```

If `./run.sh` reports "permission denied" after cloning, run `chmod +x run.sh` once (or call it as `bash run.sh ...`). `run.sh` creates a virtualenv and installs `requirements.txt` (Python 3.10+). The equivalent without it is `pip install -r requirements.txt && PYTHONPATH=src python -m pseo run`. The key can also go in `.env` (see `.env.example`, gitignored).

## Results from the real run

All 12 keywords, exactly as given, were published by `gemini-3.8-flash` (writer and planner) and checked by `gemini-3.5-flash` (editor). No manual edits.

| | |
|---|---|
| Pages published / quarantined | **12 / 0** |
| Length | 960–1,282 words per page |
| Editor scores (specificity / usefulness / accuracy, 1–5) | every page 4–5 on all three; every page passes the swap test |
| Highest 5-gram text overlap between any two pages | **0.018** Jaccard (mean 0.006; gate 0.12) |
| Verbatim shared sentences | **1** sentence in the whole corpus (gate: max 1 per page) |
| Highest semantic similarity between two pages | 0.929 cosine: *OCR handwriting iPhone* ↔ *convert scanned image to text*, the pair clustering flagged before writing (mean 0.85; gate 0.93) |
| Highest FAQ-question similarity across pages | 0.928 (gate 0.93; two true duplicates at 0.95 were caught and rewritten) |
| Drafts rejected and revised | 9: 8 by the QA gates and editor, 1 by the corpus sweep. Reasons included an invented "OCR button" and filter menu, a "guaranteed" claim, filler ("seamless", "As a student…"), two sentences copied from other pages, a too-long meta description, semantic overlap with a sibling page, and two duplicated FAQ questions |

What happened across runs (all logged in `output/report.md`):

1. The free tier's **daily quota ran out mid-run**. The circuit breaker stopped after 3 failures and recorded the 3 unwritten pages as `failed`. The next run regenerated **only those 3**.
2. While reviewing output I found the Word page calling desktop Word "free". I corrected **one fact** in `data/platform_facts.yaml`, and the next run regenerated **only that one page**, because only its brief used that fact.
3. I then added FAQ-level duplicate detection. The corpus sweep found two FAQ questions shared by the OCR and image-to-text pages and revised **only the newer page**.

Before the final run, a development run also showed the editor catching a template flaw: the *scan document without an app* page told readers to install the app (specificity 3, usefulness 2). The fix was to make the steps section follow intent (see below). That run's state was reset when the prompts changed, so it isn't in the report.

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
             in the facts its brief selected
 5 QA        rules: length, SEO fields, keyword coverage, banned filler, over-claims,
                    Pro-feature-called-free, generic headings
             duplicate gates vs the WHOLE published corpus: 5-gram Jaccard, verbatim sentences,
                    page embedding cosine, FAQ-question cosine, near-duplicate titles/metas
             LLM editor (a different model): specificity, usefulness, accuracy, swap test
               └─ any failure → the exact errors go back to the writer → 4 (max 2 revisions)
                  → still failing → QUARANTINE: not published, alerted
 6 SWEEP     all-pairs check of the final corpus; the newer page of any violating pair is revised
 7 RENDER    HTML (canonical, HowTo + FAQPage JSON-LD), Markdown, JSON, index, sitemap
 8 REPORT    report.md + similarity.csv + run history; alerts, heartbeat, exit code
             state: SQLite (pages, input hashes, shingles, embeddings, runs, events)
```

This is an agent rather than a prompt in a loop because each draft is measured, and the specific failures go back to the model as instructions. For example, *"semantically too close to /ocr-handwriting-iphone (cosine 0.935). That page already covers: … Rebuild the steps, tips and FAQs around this page's own angle: …"*. The loop runs until the page passes or is held back.

## Stack and why

| Choice | Why it fits this problem |
|---|---|
| **Python** | Mature LLM SDKs, text processing and numerics in one language, and an easy test harness. |
| **Gemini** (`gemini-3.8-flash` writer/planner, `gemini-3.5-flash` editor, `gemini-embedding-001`) | Structured output validated against pydantic schemas, embeddings from the same vendor, and a free tier for development. The editor is a different model from the writer, so it doesn't share the writer's blind spots. Each role is a **fallback chain** in `config.yaml`: if a model is overloaded (503/504, timeout) or out of daily quota, the call moves to the next model. Only `llm.py` is vendor-specific. |
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

## Scaling to 500+ keywords without reading every page

* **Gates replace reviewers.** The same checks run on page 12 and page 5,000, and only passing pages publish. Humans look at the quarantine folder and the report's most-similar pairs, which is a handful of pages rather than 500.
* **Constant-size prompts.** The planner sees at most `plan_batch_size` sibling keywords plus each one's nearest published neighbours, never the whole corpus.
* **Cheap duplicate checks.** Below 2,000 pages every pair is compared exactly (milliseconds). Above that, candidates come from MinHash-LSH (`similarity.py`, about 93% recall at Jaccard 0.2) plus each page's nearest embedding neighbours.
* **Exact incremental regeneration.** Each page stores a hash of its real inputs: keyword record, app fact sheet, **only the platform facts its brief uses**, prompt templates, and primary model names. Adding 50 keywords generates 50 pages, and editing one fact regenerates only the pages that use it (demonstrated above). Adding a fallback model regenerates nothing.
* **Multi-app.** `keywords.csv` has an `app_id` column and each app has its own fact sheet, so the whole portfolio runs through one pipeline.
* **Cost.** Measured at about 13k input and 3.5k output tokens per page, roughly 3 generate calls per page (plan share + write + editor + ~0.75 revisions) and 1–2 embedding calls. The free tier allows only about 20 requests per day per model, which is fine for development but not for 500 pages a day. Production needs a paid key; nothing else changes. `max_new_pages_per_run` caps any single run's spend.

## Running unattended

* **Schedule:** `.github/workflows/pseo.yml` runs the tests on every push and generation daily, plus a manual trigger with an optional `--force`. The API key, alert webhook and heartbeat URL are GitHub Secrets and are never stored in the repo.
* **Safe to re-run and resume:** runs are incremental by input hash. An exclusive file lock (released by the OS if the process dies) plus Actions `concurrency` mean runs never overlap. A killed run is marked `aborted` at the next start.
* **Resilient calls:** a client-side rate limiter, a 180 s hard timeout per request, retries with backoff that honour the server's `retryDelay`, and model fallback on overload or daily quota.
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
* **Thresholds were calibrated on 12 pages.** The closest pair sits at 0.929 against a 0.93 gate: these two keywords overlap in meaning, and the pages are differentiated (handwriting vs printed text) but close. At 500 pages, the report's similarity distribution would be used to re-tune thresholds, and keyword pairs like this would be candidates for merging.
* **No keyword research inputs.** Search volume, difficulty and SERP analysis would be the next input; SERP data would let the planner target gaps in what currently ranks.
* **Publishing to a CMS, Search Console and indexing are out of scope** per the brief. The output (static HTML/Markdown/JSON plus a sitemap) is what a CMS importer would consume.

## Repository layout

```
config.yaml                  models, thresholds, rate limits (env override: PSEO_<SECTION>_<KEY>)
run.sh                       one-command runner
data/keywords.csv            input keywords (+ app_id)
data/apps/doc-scanner.yaml   app fact sheet (grounding)
data/platform_facts.yaml     iOS / third-party facts for the alternatives section
prompts/                     system, plan, write (+ revision), editor prompts
templates/                   page.html.j2, page.md.j2
src/pseo/
  keywords.py   1  rules → KeywordRecord
  cluster.py    2  merge / siblings / link candidates
  agents.py   3-5  planner, writer/reviser, editor
  quality.py    5  QA gates
  similarity.py    shingles, sentences, MinHash-LSH, cosine
  pipeline.py      orchestration, revise loop, sweep, circuit breaker, incremental hashing
  render.py     7  HTML / MD / JSON / index / sitemap
  report.py     8  report.md, similarity.csv
  store.py         SQLite state
  ops.py           run lock, alerts, heartbeat
  llm.py           Gemini provider (rate limit, timeouts, retries, fallback) + offline mock
  cli.py           python -m pseo run | render | status | explain | keywords | add | remove | replace
  keyword_admin.py safe edits to data/keywords.csv (dedupe, app check, atomic write)
  explain.py       per-page trace: inputs, brief, every draft and verdict
tests/             offline end-to-end and unit tests
.github/workflows/ schedule + CI
output/            generated pages, report, manifest, sitemap, run history
state/pseo.sqlite  pipeline memory
```
