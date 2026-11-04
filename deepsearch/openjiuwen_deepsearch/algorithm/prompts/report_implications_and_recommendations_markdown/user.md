{% if audience_role %}
- **Declared target audience (use as `user_role`)**: {{ audience_role }}. Prefer this over re-inference unless sub-reports clearly contradict it.
{% else %}
You must internally determine the user's real role (occupation/identity) using the two core inputs below.
{% endif %}
- Input Information:
 - User's Original Query (user_query): {{user_query}}
 - All sub_reports

{% if tone %}
- **Tone intent**: {{ tone }}. Interpret as writing stance (English enum, e.g. objective, formal, analytical). Apply to Implications and Recommendations; stay consistent with sub-reports.
{% endif %}


{% if audience_role %}
You are preparing the final chapter for **{{ audience_role }}** (internal `user_role`). Produce a concise, decision-oriented chapter for this audience.
{% else %}
You are an {{ user_role | default("senior decision-maker") }}. Produce the final chapter for a research report. Use the inputs below to create a concise, decision‑oriented chapter suitable for senior managers, regulators, and specialist readers.
{% endif %}

Inputs:
- Research subject: [{{report_task}}]
- - Key findings: [{{current_outline}}]
- - All sub_reports


{% if task_type or has_required_dimensions or has_comparison_targets %}
Task contract for the final chapter:
- Primary task type: {{ task_type or "general_research" }}
{% if has_required_dimensions %}- Required dimensions: {{ required_dimensions_text }}{% endif %}
{% if has_comparison_targets %}- Comparison targets: {{ comparison_targets_text }}{% endif %}
{% endif %}


Main Content: {{main_content}}

- Write the entire response in the language specified by `language` = **{{language}}**.
