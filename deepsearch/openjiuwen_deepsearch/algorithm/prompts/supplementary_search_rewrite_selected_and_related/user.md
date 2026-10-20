# Input

- Requested language: {{ language }}
{% if user_instruction %}- User instruction: {{ user_instruction }} (the supplement direction explicitly provided by the user; align with it first during rewriting){% endif %}
- Selected content: {{ selected_text_clean }} (the core passage the user wants to supplement, and the **focus area** of this rewrite)
- Original section: {{ section_text_clean }} (the complete section containing the selected content, which serves as the basis of the rewrite; the output must include the full section content)
- Supplementary summary: {{ collector_summary }} (the summary of new information retrieved this time, and the main basis for the rewrite)
- Document information: {{ doc_infos }} (the source document list for the supplementary information, including title, time, and quality scores; prefer highly relevant and authoritative sources, and do not output URLs or document scores in the body text)
