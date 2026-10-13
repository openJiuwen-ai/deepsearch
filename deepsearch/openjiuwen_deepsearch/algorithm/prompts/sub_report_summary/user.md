# Input Data
1. **Sub Report Content**: (The detailed text you need to summarize).
2. **Full Report Outline**: `{{outline}}` (The structure of the complete document).
3. **User Query**: `{{user_query}}` (The core research objective).
4. **Section ID**: `{{section_id}}` (The position of this sub-report within the whole).

{% if audience_role or tone %}
## Report Detail Constraints
{% if audience_role %}
- **Target Audience**: {{ audience_role }}. Carry forward entities and conclusions this audience will need in later sections.
{% endif %}
{% if tone %}
- **Tone Intent**: {{ tone }}. Keep summary wording aligned with this stance and with the sub-report body.
{% endif %}
{% endif %}


{% if paragraph_style | default("detailed") == "concise" %}
    * **Target Range (Brief)**: 150–280 words.
    * **Hard Ceiling**: 320 words.
{% else %}
    * **Target Range**: 350-450 words.
    * **Hard Ceiling**: 500 words.
{% endif %}

Sub report content is {{sub_report_content}}

4. **Language**: Strictly use **{{language}}**.
