# Input

- Current date: {{ current_date }}
- Requested language: {{ language }}
- Paragraph to verify: {{ verified_paragraph }}
- Section heading: {{ section_heading }}
{% if user_instruction %}- User note: {{ user_instruction }}{% endif %}
- Verification reference materials: {{ doc_infos }}
  - These materials come from the report section's collected references and/or supplementary search for fact-checking
  - The end user did NOT upload or provide these documents
