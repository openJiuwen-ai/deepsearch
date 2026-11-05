# Input
- **Section Markdown**: one `## ` section (its `### ` subsections included). Inline citations look like
  `[[n]](URL)`; the report may legally have none.
- **Shell CSS**: the shell's `<style>` content — the class vocabulary you MUST reuse.
- Output language: {{ language }}.

{{ request_content }}

{% if section_id %}
You are generating section {{ section_id }}.
{% endif %}
{% if retry_feedback %}
## Validation feedback
Your previous output failed validation. Fix ALL of the following issues and regenerate:
{% for error in retry_feedback %}
- {{ error }}
{% if "chart_config" in error %}
  Fix: ECharts placeholders (`<div class="echarts-chart" data-chart-id="...">`) and the config block (`<template id="chart-configs">[...]</template>` at the end) MUST appear in pairs with matching ids. Either remove ALL placeholder divs and render those charts as CSS bar rows, or add/fix the template block so every placeholder has one config entry with the same id.
{% endif %}
{% endfor %}
{% if "truncated" in (retry_feedback | join(" ")) %}
The previous output was truncated. Reduce CSS size and use fewer charts so the full output fits.
{% endif %}
{% endif %}
