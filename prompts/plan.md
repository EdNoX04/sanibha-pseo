TASK: Plan one landing page for EACH keyword below. You are planning them together on purpose: these keywords are related, and your main job is to make sure no two pages answer the same question.

For each keyword:
1. Work out the real search intent. What is the person trying to get done, on what device, in what situation? If the keyword is ambiguous (it can mean two different jobs), say so in ambiguity_note and decide how the page handles it.
2. Choose a unique_angle: the thing this page does that none of its siblings do.
3. Split overlapping territory explicitly. If two keywords touch the same topic (for example OCR appears in several), assign the topic to ONE page as its owner. On every other page, list it under exclusions with owner_slug pointing at the owner, so the writer mentions it in one line and links instead of re-explaining it.
4. must_answer: 4-6 questions this exact searcher would ask next. They must be specific to this keyword. "Is it free?" and "Is it safe?" only count when they genuinely matter for this keyword, and must be phrased specifically (for example "Can I sign a PDF on iPhone without paying for Adobe?").
5. feature_ids: 3-5 ids from APP FACTS that actually help with this task. Prefer the most task-specific ones; supporting features (export, on-device privacy) are fine as the third or later item.
6. platform_fact_ids: ids from PLATFORM FACTS that are relevant alternatives or context.
7. related_slugs: 2-4 slugs from the CANDIDATE LINKS for this keyword that a reader would plausibly want next. Only use slugs that appear in that keyword's candidate list.

Rule-based hints are given for each keyword (intent_guess, formats, candidate_features). They are hints only; override them when they are wrong.

KEYWORDS TO PLAN (JSON):
{{ keywords_json }}

CANDIDATE LINKS PER SLUG (JSON):
{{ neighbours_json }}

ALREADY-PUBLISHED RELATED PAGES (JSON). These exist and keep their territory. Do not plan a page that answers the same questions; where a keyword overlaps one of them, add an exclusion with that page's slug as owner_slug:
{{ published_json }}

Return a PlanBatch with exactly one brief per keyword, using the slugs given.
