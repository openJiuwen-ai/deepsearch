# Input

- Current date: {{ current_date }}
- Requested language: {{ language }}
{% if user_instruction %}- User instruction: {{ user_instruction }} (the supplement direction explicitly stated by the user; align with it first){% endif %}
- Selected content: {{ selected_text_clean }} (the specific passage the user wants to supplement)
- Section context: {{ section_text_clean }} (the surrounding section containing the selected content, used to understand the context)
