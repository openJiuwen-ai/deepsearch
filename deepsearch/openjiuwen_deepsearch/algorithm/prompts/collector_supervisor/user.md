Current date: {{ current_date }}
Output language: {{ language }}
Maximum next-query count: {{ max_search_query_count }}
The allowed next_queries count range is 0..{{ max_search_query_count }}.

# Current task context

Topic:
{{ plan_title }}

Research guidance:
{{ plan_thought }}

Task title:
{{ step_title }}

Task description:
{{ step_description }}

{% if has_temporal_scope %}
## Research Time Boundary
{{ temporal_scope_instruction }}
- Interpret "latest" as the latest information available within this boundary.
- {{ temporal_query_instruction }}
- Do not use provider-specific filter syntax (e.g. engine date parameters); only natural-language time phrases are allowed.
- A next query may contain at most five topical keywords; the time phrase does not count toward the five topical keywords.
{% endif %}
{% if has_temporal_scope == false %}
Query should ensure that the most current information available as of {{ current_date }} is gathered.
{% endif %}

# Collector Ledger

Ledger brief:
{{ ledger_brief }}

# Compact evidence table

{{ evidence_table }}

{% if material_evidence %}
# Bound user-material evidence
{{ material_evidence | tojson }}

Treat this as already available evidence. Only request follow-up web searches for claims, validation, or gaps that it
does not cover; do not mark the step insufficient merely because a fact is absent from the web evidence table.
{% endif %}
