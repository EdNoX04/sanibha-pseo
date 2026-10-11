TASK: Write the landing page for the keyword "{{ record.keyword }}".

PAGE BRIEF (from the planning step; follow it):
{{ brief_json }}

KEYWORD DATA:
- platform: {{ record.platform }}
- searcher asked for free: {{ record.wants_free }}
{% if record.merged_keywords %}- also answer these near-identical searches on this same page: {{ record.merged_keywords | join(", ") }}
{% endif %}
SIBLING PAGES (already exist or are being written; do not duplicate them, link instead):
{% for s in siblings %}- /{{ s.slug }}: "{{ s.keyword }}" (owns: {{ s.angle }})
{% endfor %}

SECTION REQUIREMENTS:
- title: <= 60 characters, contains the core of the keyword, written for a human click (no pipes, no brand stuffing).
- meta_description: 110-155 characters, states the concrete outcome.
- h1: natural-language version of the keyword; may differ from the title.
- quick_answer: 40-60 words that fully answer the query on their own. Start with the answer, not with context.
- intro: 2-3 short paragraphs. Open with the searcher's situation from the brief's scenario, then say what this page covers. Do not restate the quick answer.
- steps_heading / steps: 3-8 steps for the method that best serves THIS searcher's intent, in the order a user really taps through. Each detail must say what to do AND what to look out for at that step. Which method that is depends on intent_type:
  - task_howto, conversion, problem_fix: the steps use the app (only features from the brief's feature_ids).
  - no_app_alternative: the searcher explicitly does not want to install an app, so the steps use the built-in method (from PLATFORM FACTS). The app appears only in alternatives, feature points and CTA, framed as "if you need more than the built-in tool".
  - comparison: the steps are how to evaluate and choose (what to test, in what order, with a sample document). Do not rank or name third-party apps that are not in the facts.
- alternatives_heading / alternatives: 2-4 other ways to do it, including free built-in iOS options where they exist (or the app, when the steps used a built-in method), each with an honest limitation. If a built-in option is genuinely good enough for some people, say so; that honesty is what makes the page trustworthy. Never claim the app is the only way.
- features_heading / feature_points: 3-5 items, one per feature_id from the brief. The benefit must be about THIS task (for example, for receipts: faded thermal paper), not a generic description of the feature.
- tips_heading / tips: 3-5 practical tips or fixes for problems specific to this task.
- faqs: 4-6 question/answer pairs built from the brief's must_answer. Answers 2-4 sentences, self-contained.
- cta: a short heading and one or two sentences pointing to the app, tied to this task. No hype.
- Headings must be specific to this page (for example "How to scan a receipt with Document Scanner" rather than "How it works").

Length: 800-1300 words across all fields.
Where the brief lists exclusions, mention each excluded topic in at most one sentence.
{% if feedback %}

THIS IS A REVISION. The previous draft failed automated quality checks. Fix every problem below. Keep what was good, but rewrite any sentence listed as duplicated so that it says something specific to THIS keyword.
PROBLEMS:
{% for f in feedback %}- {{ f }}
{% endfor %}
PREVIOUS DRAFT (JSON):
{{ previous_json }}
{% endif %}
