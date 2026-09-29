Current date (UTC): {{ current_date }}

Original user request: {{ original_query }}
Research question: {{ questions }}
Output language: {{ language }}
All section titles, descriptions, and the thought field must use {{ language }}.
User-provided outline: {{ user_outline }}
Current outline: {{ current_outline }}
Entry search results: {{ entry_search_results }}
{% if audience_role %}Target audience: {{ audience_role }}. Preserve this audience orientation when refining wording and section focus.{% endif %}
{% if tone %}Writing tone: {{ tone }}. Keep title/description style consistent with this tone unless user edits explicitly override it.{% endif %}
