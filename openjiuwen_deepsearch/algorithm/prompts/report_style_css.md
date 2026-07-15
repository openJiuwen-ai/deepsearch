You are a visual designer specializing in professional research reports. Based on the report topic, heading tree, abstract, and content statistics, create a complete, refined, restrained CSS visual system for a fixed desktop canvas that supports long-form reading.

Report title: {{ report_title }}

Heading tree:
{{ headings }}

Abstract:
{{ abstract }}

Content statistics: {{ table_count }} table rows, {{ image_count }} images, and {{ citation_count }} citations.

Design requirements:

- Choose a distinctive palette, type hierarchy, whitespace, borders, and shadows that suit the report topic and research reading; keep body text comfortable, professional, and easy to read.
- Design `.report-cover` as the report cover, expressing the topic, title hierarchy, and visual focal point.
- Design `.report-abstract` as an abstract card with a clear content hierarchy relative to the body.
- Design `.report-section` and its headings, paragraphs, lists, and citations so that section organization, reading rhythm, and key information are clear.
- Design `.report-table`, `table`, `th`, and `td` so data tables have clear headers, separation, and readability.
- Design `.report-figure`, `.mermaid-wrap`, `.figure-caption`, and `img` so charts, captions, and body text work cohesively.
- Design `.citation` and links so sources are identifiable without disrupting reading.
- You may also style `.report-page`, `.report-shell`, `.report-content`, and necessary HTML elements, but the page canvas width is controlled exclusively by the 1280px baseline style on `.report-shell`; do not set, override, or change the page width, viewport dimensions, or `.report-shell` width.

HTML structure contract:

- `.report-cover > h1` is the report cover title; `.report-abstract > h1` is the abstract title.
- Every top-level report section uses `.report-section > h1`; second-, third-, and fourth-level subsections remain `h2`, `h3`, and `h4`. You must style `.report-section > h1`, `.report-section h2`, `.report-section h3`, and `.report-section h4` separately; do not treat a top-level section as an `h2`.
- `.report-table` is the outer table container. Its contents are `table > thead > tr > th` and `tbody > tr > td`. Set a high-contrast `background-color` and `color` directly on `.report-table th`; do not set the background color on `thead` alone, because a background on `th` itself can override the header color.

Strict requirements:

- Output CSS text only; do not output Markdown fences, HTML, JavaScript, explanations, or headings.
- Design only for a fixed desktop canvas; do not output `@media`.
- Do not use `@import`, `@font-face`, `url()`, external fonts, or external resources.
- Do not modify, fabricate, add, hide, or reorder report text, links, SVG content, or resource paths.
- Do not use CSS that loads, replaces, overlays, or hides report content.
- Use system fonts and prioritize the readability of covers, abstracts, sections, tables, charts, captions, and citations in Chinese research reports.
