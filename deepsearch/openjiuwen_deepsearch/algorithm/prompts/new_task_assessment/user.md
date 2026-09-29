# Input

- Current date: {{ current_date }}
- Requested language: {{ language }}
{% if user_instruction %}- User instruction: {{ user_instruction }}{% endif %}
- Selected text: {{ selected_text }}
- Current section (clean): {{ clean_section_text }}
- Section title: {{ section_title }}
- Historical doc infos (indexed from 1 in listed order): {{ historical_doc_infos }}
- Supported edit strategies: {{ supported_edit_strategies }}
