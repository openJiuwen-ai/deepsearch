# Role & Objective
Convert ONE section of a markdown research report into an HTML fragment (body content only). The report shell —
design system CSS, hero, summary, table of contents — already exists; your fragment will be inserted into its
`#brief-sections` mount point.

# Input
- **Section Markdown**: one `## ` section (its `### ` subsections included). Inline citations look like
  `[[n]](URL)`; the report may legally have none.
- **Shell CSS**: the shell's `<style>` content — the class vocabulary you MUST reuse.
- Output language: {{language}}.

{% include "brief_html_common.md" %}

# Fragment Rules

## 1) Content Fidelity & Section Wrapper
- Every fact, number, and conclusion must come from the Section Markdown. Do not invent data or introduce
  external information.
- Keep heading order and hierarchy: the section's `## N Title` → `<h2>` (the first element of the fragment),
  `### ` subsections → `<h3>`, in order.
- The ENTIRE fragment must be wrapped in one `<section class="section">` card (the shell defines it):
  `<h2>` sits inside the card, all content follows within the same card. Never leave `<h2>` floating on the
  page background, and never scatter the section's content across multiple top-level cards. Do not add the
  `.card` class to `.section` and do not add a generic `.card` directly inside it. The system supplies the stable
  TOC `id`; do not invent a different section anchor.

## 2) Visualization — prefer CSS-first, ECharts only when necessary
Choose the simplest technique that presents the data well. CSS visualization is more reliable and renders
instantly; use it for the majority of visuals.

- **CSS bar rows (default for comparisons and shares)**: horizontal bars made of plain divs. Put every readable
  category, value, and status in `.bar-label`; `.bar-fill` is a visual-only fill and MUST contain no text or
  nested content. Use category-tinted gradient fills:
  ```html
  <div class="bar-row">
    <span class="bar-label"><span class="name">产品A</span><span class="num">82%</span></span>
    <div class="bar-track"><div class="bar-fill b1" style="width:82%"></div></div>
  </div>
  ```
  When entities fall into 2-5 groups (e.g. vendors, regions, categories), use the `.b1`–`.b5` tint classes
  instead of semantic good/warn/risk colors. Use for score comparisons, market shares, rankings, and any small
  set of numbers.
- **Chart-with-source + interpretation pairing**: wrap ECharts and non-trivial visual comparisons in one
  `<div class="chart-card">` containing, in order: `.chart-title` (bold title), `.chart-source` (small muted
  line naming the data source as given in the markdown, e.g. `Data: xxx` or `来源：xxx`), the visual, then
  `.takeaways` — 2-3 tight bullets, each starting with a bold lead-in (a conclusion, not a description).
  Simple metric cards and short CSS bar groups may use a compact source line without a separate takeaways card.
- **ECharts data chart (at most ONE per fragment)**: only for a rich interactive chart that CSS bars cannot
  express — line/area trends over many points, multi-series comparisons, radar, scatter, or pie/donut with many
  slices. Prefer CSS bars whenever they suffice. Chart data must be numbers that appear in the markdown.
  An ECharts chart consists of a placeholder element AND one entry in the config template — they MUST appear in
  pairs with matching ids; if you render only CSS charts, output no `echarts-chart` elements at all.
- **ECharts data integrity (HARD)**: treat `xAxis.data` as the complete categorical axis. Every `series.data`
  array MUST have exactly one item per category and use the same order. Unknown values MUST be `null`; never use
  zero, interpolation, extrapolation, or a shorter array to stand in for missing observations. Never set
  `connectNulls: true`. If a line/area series has an internal missing category, preserve an explicit gap only for
  ordinary measurements. For ratio/share/rate/percentage series (including GDP share), omit the whole series
  and render the known values as a table, metric cards, or unconnected scatter points. Do not combine estimates
  from different sources or denominators into one series; split scenarios or use a table instead. When these
  rules cannot be satisfied, prefer no ECharts chart over a misleading continuous trend.
- **Timeline (for evolution/history content)**: when the markdown describes a progression (stages, eras,
  milestones), render it as a vertical timeline: left border line + dot markers, each item = date (small,
  accent color, uppercase) + title (bold) + 1-2 sentence body; mark critical turning points in a warning
  color class.
- **Entity cards (for small structured comparisons)**: when the markdown compares 2-5 entities with the same
  fields (products, methods, vendors), render each entity as a card: entity name (bold, numbered ①②③) + a
  label/value grid + a one-line note, instead of a wide table. For more than 4 entities or more than 3 fields,
  prefer one comparison table and do not duplicate it with entity cards or bars.
- **Diagrams (HTML+CSS only)**: flows and relationships drawn with flex/grid layout, borders, and CSS arrows.
  Never use ECharts for flowcharts.
- **Layout elements**: key-conclusion cards, big-number metric cards, highlighted quote blocks, comparison
  columns, badges, and timelines that improve scannability. Use at most two primary visual blocks in this section
  and at most one in any `###` subsection. If the same data can be expressed with prose or a table, do not add
  another chart merely for decoration.

## 3) Use the Shell CSS
- Reuse the classes defined in the provided Shell CSS, following the shared component vocabulary verbatim.
  One-off tweaks use inline `style` attributes. Never output a `<style>` block or document-level elements
  (`<html>`, `<head>`, `<body>`).

## 4) Citation Boundary
- Preserve citation markers `[[n]](URL)` in the section content exactly as supplied. Do not convert, renumber,
  remove, or invent them; the Python assembly stage performs the deterministic HTML conversion.
- Do NOT render a references section or reference entries — the system renders them from the report's registry.
- If a citation number or any character of its URL is changed accidentally, the Python assembly stage removes the
  complete marker from the body and continues; this does not make the section fail or trigger a retry.

## 5) Chart Styling
- Specify chart colors and fonts explicitly in ECharts `option` or inline styles; do not rely on default themes.
- Keep ECharts ids simple (`c1`); the system renames chart ids and merges configs across sections automatically.

## 6) Output Format
- Output exactly one `<html_section>...</html_section>` block containing the fragment HTML, with no extra
  explanation text outside the block.
- Fragment = body elements only (`div`, `section`, `h2`–`h4`, `p`, `ul`, `ol`, `li`, `table`, `thead`, `tbody`,
  `tr`, `th`, `td`, `strong`, `em`, `blockquote`, `a`, `sup`, `sub`, `span`, `header`, `footer`, `nav`, `hr`,
  `br`).
