# Shared HTML Contract

The two HTML prompts are independent stages of one report pipeline. You are a senior data-visualization editor;
`brief_html_reporter` owns the page shell, while `brief_html_section` owns one section fragment. Respect the
assigned boundary and apply the shared contract below in either stage.

## Shared Delivery Contract

- The result belongs to one **single-file**, **self-contained**, **offline** HTML report.
- Use no CDN, remote images, external fonts, external CSS, or external JavaScript. Never use `url()` in CSS.
- Keep the HTML safe for deterministic sanitization and system-side assembly. Do not rely on browser-side fetching.

## Zero-Script Contract (HARD — violations are cleaned or rejected automatically)

- Neither prompt may output `<script>` tags, `on*` event attributes, `javascript:` URLs, `<iframe>`, `<object>`,
  `<embed>`, or `<img>`.
- The ECharts library and initialization scripts are injected by the system only when a valid chart is present;
  do not write them yourself.

## Shared Component Vocabulary

- The shell owns the CSS definitions; section fragments reuse these class names verbatim:
  `.card`, `.grid-2`, `.bar-row`, `.bar-label`, `.bar-track`, `.bar-fill`, `.b1`–`.b5`, `.badge`,
  `.metric-grid`, `.metric-card`, `.label`, `.value`, `.unit`, `.sub`, `.timeline`, `.quote-block`,
  `.entity-card`, `.chart-card`, `.chart-title`, `.chart-source`, `.takeaways`, `.takeaways-title`,
  `.section`, `.cite-ref`, `.references`, `.table-wrap`, `.name`, `.num`, and `.echarts-chart`.
- Use `.section` as the only outer card for one report section. Do not combine it with `.card` and do not put
  a generic `.card` directly inside it. Component cards such as `.metric-card`, `.entity-card`, and `.chart-card`
  are allowed inside a section when the content decision below calls for them.
- `.bar-label` carries every readable category, value, or status. `.bar-track` is intentionally thin and
  `.bar-fill` is a visual-only fill that MUST contain no text or nested content; never put readable text inside
  `.bar-fill`, because the filled width can be too narrow and the shell may clip it.
- A section chart is wrapped in one `.chart-card` containing `.chart-title`, `.chart-source`, the chart itself,
  and, for a non-trivial chart, `.takeaways` in that order. Simple metric cards and short CSS bar groups may
  stay in the section flow without an additional interpretation card.

## Visual Density and Responsive Contract

- Present each dataset in one primary visual form. Do not repeat the same values as metric cards, CSS bars,
  ECharts, and a second summary block.
- Prefer readable prose, lists, and tables for context; visual components support the conclusion rather than
  replacing the explanation. Keep a section to at most two primary visual blocks and a subsection to at most one.
- Every grid child must allow `min-width:0`; long source URLs, labels, and entity names must wrap. Put wide tables
  inside `.table-wrap` with horizontal scrolling rather than widening the page.
- Use the topic accent and its tints for ordinary categories. Reserve good/warn/risk colors for actual semantic
  status, not for arbitrary category decoration.

## ECharts Boundary

- The shell prompt MUST NOT render a chart, chart placeholder, or chart configuration. It only defines the CSS
  and mount points needed by section fragments.
- The section prompt may use ECharts only when CSS cannot express the data clearly. A chart consists of a matching
  placeholder and one config entry at the end of the fragment:
  ```html
  <div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>
  <template id="chart-configs">[{"id":"c1","option":{...}}]</template>
  ```
- `option` must be strict JSON: double quotes, no trailing commas, comments, functions, URLs, or HTML tags in
  string values. Use `tooltip: {"renderMode": "richText"}`; the system merges it deterministically.
- Chart values must come from the relevant Markdown content. The system renames chart ids, validates the option,
  removes an invalid chart without discarding the surrounding section, and injects the local ECharts runtime only
  when a valid chart configuration survives assembly.
