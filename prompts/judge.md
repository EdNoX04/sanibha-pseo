TASK: You are a strict editor reviewing an auto-generated SEO landing page before it is published without human review. Be critical: an average page should score 3.

Target keyword: "{{ record.keyword }}"
Searcher goal (from the brief): {{ brief.searcher_goal }}
Sibling keywords on the same site: {{ sibling_keywords | join("; ") }}

Score:
- specificity (1-5): 5 = every section is clearly about this exact keyword; 1 = generic text that would fit any document-scanner page.
- usefulness (1-5): 5 = a reader can complete the task and avoid the common mistakes using only this page.
- accuracy_risk (1-5): 5 = no claims that look invented, exaggerated, or legally/regulatorily risky (passport rules, "100% accurate", invented prices).
- swap_test: imagine replacing the keyword with one of the sibling keywords. "pass" if the page would then be clearly wrong, "fail" if it would still mostly read fine.
- problems: list concrete, fixable problems (quote the offending phrase). Empty list if none.

PAGE (JSON):
{{ content_json }}
