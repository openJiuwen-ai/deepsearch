Output language: {{ language }}

## Queries
{% for q in queries %}
- {{ q }}
{% endfor %}

## Tools
{% for tool in tools %}
- `{{ tool.name }}`: {{ tool.description }}
  schema: {{ tool.input_schema | tojson }}
{% endfor %}
