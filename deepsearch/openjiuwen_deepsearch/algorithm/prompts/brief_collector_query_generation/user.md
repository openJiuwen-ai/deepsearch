# Research Contract

<current_date>{{ current_date }}</current_date>
<outline>{{ outline | tojson }}</outline>
{% if material_first %}
- This is a material-first report. The supplied user materials are the primary evidence. Generate web queries only for
  claims, dimensions, or research gaps that the material analyses do not cover; do not re-search material-supported
  claims merely for corroboration.
- Retain a query for a material-backed section only when its outlined step is explicitly outside the listed supported
  claims or gaps. Prefer the smallest possible set of such gap-filling queries.
<material_first>true</material_first>
<user_material_analysis>{{ materials_analysis_text }}</user_material_analysis>
<user_material_relevance>{{ materials_relevance_text }}</user_material_relevance>
{% endif %}
<research_intent>
<task_type>{{ task_type }}</task_type>
<required_dimensions>{{ required_dimensions | tojson }}</required_dimensions>
<comparison_targets>{{ comparison_targets | tojson }}</comparison_targets>
</research_intent>

{% if task_type == "comparison" %}
- For comparison tasks, include the relevant targets and comparison dimension where this improves retrieval precision.
{% elif task_type == "trend_judgement" %}
- For trend-judgement tasks, target dated evidence for current status, drivers, bottlenecks, and relevant feasibility
  signals.
{% endif %}

{% if has_temporal_scope %}
<temporal_scope>{{ temporal_scope_instruction }}</temporal_scope>
- Express the requested period naturally in relevant queries; do not use provider-specific filter syntax.
{% endif %}

<executed_queries>{{ executed_queries | tojson }}</executed_queries>
<blocking_gaps>{{ blocking_gaps | tojson }}</blocking_gaps>
<user_request>{{ user_query }}</user_request>
