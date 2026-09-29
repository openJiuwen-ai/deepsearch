# Input

- Requested language: {{ language }}
{% if user_instruction %}- User instruction: {{ user_instruction }} (the rewrite direction explicitly expressed by the user; highest priority){% endif %}
- Selected content: {{ selected_text_clean }} (the original passage to be rewritten)
- Original section (context): {{ section_text_clean }} (the section containing the selected content, used to understand the surrounding context; **not part of the output range**)
- Supplementary summary: {{ collector_summary }} (the summary of newly retrieved information, which is the main source material for the rewrite)
- Document information: {{ doc_infos }} (metadata of the source documents, including title, time, relevance, and other scores; **use only to judge information credibility, and do not cite URLs in the body text**)
