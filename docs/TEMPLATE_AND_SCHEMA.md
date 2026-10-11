# Page template and data schema

## 1. Page template

Every page has the same ten sections in the same order. What changes per keyword is the content inside them, which is planned per keyword and checked for overlap with every other page.

| # | Section | Field(s) | Why it's there |
|---|---|---|---|
| 1 | Title + meta description | `title` (≤60 chars), `meta_description` (110–160) | Search-result snippet. Checked for length, keyword coverage, and near-duplicates against every other page's title and meta. |
| 2 | H1 | `h1` | Natural-language version of the keyword; may differ from the title. Title + H1 must cover ≥75% of the keyword's content words. |
| 3 | Quick answer | `quick_answer` (40–60 words) | Answers the query in the first screen (featured-snippet target). Most searchers want the answer, not an essay. |
| 4 | Intro | `intro[]` (2–3 paragraphs) | Starts from the searcher's situation (the brief's `scenario`), which is the most keyword-specific text on the page. |
| 5 | Steps for the best method | `steps_heading`, `steps[]` (3–8) | The job to be done. Each step says what to do and what to watch for. Usually the app, but intent decides: for *scan document without an app* the steps use the built-in Notes/Files scanner (and the app moves to alternatives), and for *best free PDF scanner app* they are an evaluation checklist. The LLM editor caught exactly this mismatch in an early run. Emitted as `HowTo` JSON-LD. |
| 6 | Other ways to do it | `alternatives_heading`, `alternatives[]` (2–4: method / how / best for / limitation) | Honest comparison, including free built-in iOS tools. This section is the main source of differentiation, because the alternatives for "sign a PDF" (Markup) and "compress a PDF" (none built in) really are different. It is also what makes the page trustworthy rather than an ad. |
| 7 | Features that help | `features_heading`, `feature_points[]` (3–5, by feature id) | Ties the app to this task. Feature names come from the fact sheet at render time, so the model cannot rename or invent them. |
| 8 | Task-specific tips | `tips_heading`, `tips[]` (3–5) | Troubleshooting for this task (faded thermal receipts, cursive handwriting, email size limits). High value, hard to template. |
| 9 | FAQ | `faqs[]` (4–6) | Built from the brief's `must_answer` questions. Emitted as `FAQPage` JSON-LD. |
| 10 | CTA + related guides | `cta`, links from `related_slugs` + exclusion owners | Conversion, plus internal links. A sibling topic is linked instead of re-explained, which is part of the duplicate-content strategy. Links are only rendered to pages that are actually published. |

Every section heading except "Frequently asked questions" is written per page. A heading like "How it works" fails QA.

**Why this structure:** it follows how someone with a task reads: answer → how → is there a free/built-in way → why this app → what goes wrong → remaining questions → next step. A fixed skeleton keeps 500 pages consistent and machine-checkable. The variable parts sit in the sections that depend on the task (alternatives, tips, FAQ), not in the wording around the keyword.

## 2. Data schema

Three layers, each derived from the one before (`src/pseo/models.py`).

### Layer 1: `KeywordRecord`, deterministic (rules, no LLM)

| Field | Example (`convert PDF to Word free`) | Derived how |
|---|---|---|
| `keyword` | `convert PDF to Word free` | input, exactly as given |
| `app_id` | `doc-scanner` | input CSV column (multi-app portfolio) |
| `slug` | `convert-pdf-to-word-free` | lowercase, non-alphanumerics → `-` |
| `tokens` | `[convert, pdf, word, free]` | content words, stopwords removed |
| `platform` | `any` | `iphone/ios/ipad` → `iphone` |
| `wants_free` | `true` | the word "free" is present |
| `intent_guess` | `conversion` | regex rules: best/top/vs → comparison; "without an app" → no_app_alternative; remove/fix/shadow → problem_fix; convert / "to word/text" → conversion; else task_howto |
| `action` | `convert` | first word |
| `source_format` / `target_format` | `pdf` / `word` | format words before/after "to" |
| `candidate_features` | `[ocr, word_export]` | feature `triggers` from the app fact sheet found in the keyword |
| `siblings` | `[convert-scanned-image-to-text, …]` | clustering stage (embedding similarity) |
| `merged_keywords` | `[]` | near-identical keywords folded into this page |

These are rules because they must be stable: the slug is a URL, and these fields feed the input hash that decides when a page is regenerated.

### Layer 2: `PageBrief`, from the planner agent (one LLM call per batch of related keywords)

`intent_type`, `searcher_goal`, `scenario`, `unique_angle`, `must_answer[]`, `exclusions[] {topic, owner_slug}`, `feature_ids[]`, `platform_fact_ids[]`, `related_slugs[]`, `ambiguity_note`.

The planner sees all related keywords at once and decides who owns which subtopic. OCR, for example, belongs to one page; the others mention it in one line and link. After the LLM call, the brief is sanitized in code: unknown feature ids are dropped, and links are restricted to real neighbouring pages.

### Layer 3: `PageContent`, from the writer agent

The ten sections above as typed fields. Gemini's structured-output mode returns JSON that is validated against this pydantic model, so a malformed answer is retried, never rendered.

### Grounding data

* `data/apps/<app_id>.yaml`: the app's features, with `tier` (free/pro) and `triggers`. **The only source the agent may use for claims about the app.** One file per app in the portfolio.
* `data/platform_facts.yaml`: facts about iOS built-ins and third-party options, used in the alternatives section.

### Stored per page (SQLite `pages` table)

`status` (passed / quarantined / failed / merged / retired / needs_revision), `input_hash`, the three JSON layers, `body_text`, shingle hashes, page embedding, FAQ-question embeddings, `qa_json` (all metrics), `judge_json`, `attempts`, `error`.

Also stored: `runs` and `events` (every attempt and verdict, used by `explain` and the report) and `embed_cache` (keyword vectors keyed by model + text, so an unchanged run makes no embedding calls).

### QA result (`QAResult`, one per attempt)

| Field | Meaning |
|---|---|
| `passed` | all gates passed |
| `errors` | each failure as an instruction the writer can act on, e.g. *"FAQ '…' asks the same thing as /sign-pdf-on-iphone-free's FAQ '…'"* |
| `warnings` | logged, not blocking |
| `metrics` | word count, max Jaccard / cosine / FAQ cosine and against which page, pages compared |
| `overlaps` | verbatim sentences shared with other pages |
| `fix_fields` | the `PageContent` fields the errors live in (e.g. `["faqs"]`, `["meta_description"]`) |
| `page_level` | true when an error is about the page as a whole (meaning too close to another page, length, editor rejection) |

The pipeline routes on the last two: if `page_level` is false and `fix_fields` is non-empty, only those fields are regenerated (a "patch", using a schema built on the fly with just those fields) and merged back; otherwise the page is rewritten.
