TASK: Fix specific fields of the landing page for "{{ record.keyword }}". The rest of the page already passed every check, so change ONLY the fields listed below and return only those fields.

FIELDS TO REWRITE: {{ fields | join(", ") }}

PROBLEMS FOUND BY THE AUTOMATED CHECKS (fix every one):
{% for f in feedback %}- {{ f }}
{% endfor %}
{% if siblings %}
RELATED PAGES THAT ALREADY EXIST (do not repeat their questions or wording; link instead):
{% for s in siblings %}- /{{ s.slug }}: "{{ s.keyword }}"{% if s.faqs %} — its FAQ questions: {{ s.faqs | join(" | ") }}{% endif %}
{% endfor %}{% endif %}
Keep the same facts, tone and section requirements as the original brief:
{{ brief_json }}

CURRENT PAGE (JSON), for context:
{{ page_json }}
