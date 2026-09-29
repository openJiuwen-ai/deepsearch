# Input

- Requested language: {{ language }}
{% if user_instruction %}- User instruction: {{ user_instruction }}{% endif %}
- Edit strategy: {{ edit_strategy }}
{% if major_section_title %}- Major section title: {{ major_section_title }}{% endif %}
{% if major_section_text %}- Current major section (clean): {{ major_section_text }}{% endif %}
- Section title: {{ section_title }}
- Original section (clean): {{ clean_section_text }}
{% if selected_subsection_title %}- User selected subsection title: {{ selected_subsection_title }}{% endif %}
{% if new_subsection_title %}- New subsection title (must be used exactly): {{ new_subsection_title }}{% endif %}
- User selected focus text (clean): {{ clean_selected_text }}
- Available doc infos: {{ doc_infos }}
