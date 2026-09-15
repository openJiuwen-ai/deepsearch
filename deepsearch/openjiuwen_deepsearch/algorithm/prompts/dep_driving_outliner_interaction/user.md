Current date (UTC): {{ current_date }}

Original user request: {{ original_query }}
Research question: {{ questions }}
Output language: {{ language }}
Current outline: {{ current_outline }}
Current user feedback: {{ user_feedback }}
Previous user feedback: {{ previous_feedback }}
Reference report template: {{ report_template }}
Maximum number of sections: {{ max_section_num }}. You may add, merge, remove, or reorder sections to satisfy feedback,
but the final number of chapters must not exceed this value.
Entry search results: {{ entry_search_results }}
{% if audience_role %}Target audience: {{ audience_role }}. Keep section objectives aligned with this audience's decision priorities.{% endif %}
{% if tone %}Writing tone: {{ tone }}. Keep section organization and narrative stance consistent with this tone.{% endif %}
