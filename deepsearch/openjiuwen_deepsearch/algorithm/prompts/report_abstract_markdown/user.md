{% if audience_role or tone %}
## Report Detail Constraints
{% if audience_role %}
- **Target Audience**: {{ audience_role }}. Lead with conclusions this audience needs for decisions; avoid textbook-style background.
{% endif %}
{% if tone %}
- **Tone Intent**: {{ tone }}. Interpret as writing stance (English enum, e.g. objective, formal, analytical). Stay consistent with sub-reports; do not shift style.
{% endif %}
{% endif %}



Main Content: {{main_content}}

- The language of generated content is specified by language = **{{language}}**
