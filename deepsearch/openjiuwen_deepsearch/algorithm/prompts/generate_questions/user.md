Original user query:
{{ query }}

Output language: {{ language }}
Requested report type: {{ report_type }}

Entry search results:
{{ entry_search_results }}

{% if report_type is none %}
Since the requested report type is not specified, Question 1 MUST ask the user to choose between the professional and
brief report types (or their localized equivalents, such as 专业版 and 精简版). If Question 1 does not ask for this choice,
the output is invalid.
{% endif %}
