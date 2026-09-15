Material title: {{ material_title }}
Summary token limit: {{ SUMMARY_MAX_TOKENS }}

## Original research query

```
{{ original_query }}
```

## Partial summaries

{% for partial in partial_summaries %}
### Part {{ loop.index }}

{{ partial }}

{% endfor %}
