Main Content:
{{ main_content }}

Output language must be **{{ language }}**.

{% if audience_role or tone or user_format %}
## Report Detail Constraints
{% if audience_role %}
- **Target audience**: {{ audience_role }}. Prioritize the conclusions this audience needs to act on.
{% endif %}
{% if tone %}
- **Tone intent**: {{ tone }}. Keep wording aligned with this stance and with the chapter bodies.
{% endif %}
{% if user_format %}
- **Format constraints**: {{ user_format }}. Apply them when they are compatible with the executive-summary contract.
{% endif %}
{% endif %}
