# Input
- **Report title**, **Executive Summary Markdown** (starts with a `## 核心摘要`-style heading; may be empty),
  ordered **Section titles** (numbered, for the table of contents).
- Output language: {{ language }}.

{{ request_content }}

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
