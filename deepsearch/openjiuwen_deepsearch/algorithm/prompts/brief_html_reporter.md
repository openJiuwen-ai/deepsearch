# Role & Objective
Generate the SHELL of the HTML research report: the design system, the hero/summary card, and the table of
contents. Section bodies and the references list are generated separately by the system and inserted into your
shell — never write them yourself.

# Input
- **Report title**, **Executive Summary Markdown** (starts with a `## 核心摘要`-style heading; may be empty),
  ordered **Section titles** (numbered, for the table of contents).
- Output language: {{language}}.

{% include "brief_html_common.md" %}

# Shell Rules

## 1) Design System (required)
Define one coherent design system in a single `<style>` block, based on this template:

```css
:root {
  --bg: #ffffff; --surface: #f8f7f5; --border: #e8e6e0;
  --text: #1a1a1a; --text-muted: #6b6b68;
  --accent: <TOPIC ACCENT>; --accent-light: <lighter tint of the same hue>;
  --good: #0F6E56; --warn: #BA7517; --risk: #D85A30;
  --radius: 12px; --radius-sm: 8px;
}
```

**You MUST choose `--accent` by report topic — do NOT default to purple.** Pick a hue that fits the domain,
e.g.:

| Topic domain | Accent example |
|---|---|
| Finance / market / competition | deep blue `#1F5FBF` |
| Healthcare / silver economy / wellness | teal green `#0E7C6B` |
| Consumer / lifestyle / youth | warm orange `#C2571B` |
| Tech / AI / engineering | indigo `#3D56D6` |
| Policy / public sector | navy `#1F4E79` |
| Environment / energy | forest `#2E7D4F` |

Use `--accent-light` as a ~90% lighter tint of the same hue (mix with white). Keep all other tokens as-is.

- **Section cards**: white cards (`--bg`) on a soft page background (`--surface`), `--radius` corners,
  1px `--border`, generous padding (1.5-2rem), subtle hover border-color change to `--accent`.
- **Badges**: pill-shaped (border-radius 999px, small padding) with light tinted backgrounds and dark text of
  the same hue, e.g. `background: var(--accent-light); color: var(--accent);`
- **Metric cards**: label (small, `--text-muted`) + value (large, bold) + optional CSS bar. Group 2-4 metrics
  per row with CSS grid.
- **Tables**: `--accent-light` header background with `--accent` text, 1px row borders, row hover background.
- Typography: system font stack, 15px base, line-height 1.7, one accent color carried through headings,
  links, and bars. Never use more than one accent color family.

## 2) Component CSS Vocabulary (required — section fragments reuse these exact classes)
The `<style>` block MUST define every class listed in the shared component vocabulary, with responsive and
readable styling appropriate to its role. In particular, implement the card, grid, metric, bar, timeline,
entity, quote, chart, section, citation, references, and ECharts container styles used by section fragments.
Also include `html { scroll-behavior: smooth; }` for in-page TOC navigation.

For CSS bars, make `.bar-track` a thin 8-10px visual track and make `.bar-fill` a visual-only block. All
readable category, value, and status text belongs in `.bar-label`; `.bar-fill` MUST contain no text or nested
content, and must not be used as a text container.

## 3) Hero / Summary Card
- Hero card at the very top — the visual anchor of the whole report. It MUST be a full-width card filled with
  a deep accent gradient (e.g. `background: linear-gradient(135deg, var(--accent) 0%, <darker shade of the
  same hue> 100%)`), white text (`color: #fff`), `--radius` corners, generous padding (2-2.5rem), and two
  soft decorative circles via `::before`/`::after` (`background: rgba(255,255,255,0.05–0.08);
  border-radius: 50%`, large, partially overflowing and hidden by `overflow: hidden`). NOT a plain white
  card, NOT a thin left border — the hero must carry the accent color at full strength.
- Inside the hero: report title as `<h1>` (white, large), a one-line takeaway (white, ~92% opacity), and 2-4
  highlight chips — semi-transparent white pills on the dark hero (e.g. `background:
  rgba(255,255,255,0.16); border: 1px solid rgba(255,255,255,0.25); color: #fff; border-radius: 999px`),
  not the light-tinted `.badge` style.
- Then render the Executive Summary Markdown faithfully in a white `.card`: keep its `## 核心摘要` heading as
  a visible `<h2>` followed by the summary content (paragraphs, lists, tables as needed).

## 4) Table of Contents
- A card right after the summary listing every section title in order, with its number.

## 5) Mount Points (HARD — the system inserts content here)
- Exactly one empty `<div id="brief-sections"></div>` where section bodies will be inserted (after the TOC,
  inside the main content `<div>` container).
- Exactly one empty `<div id="brief-references"></div>` at the end of that main content `<div>`; the system inserts
  the references list there (style `.references` accordingly). When the report has no references the system
  removes this div.
- Do NOT write any section body, reference entry, or chart yourself.

## 6) Citation Boundary (summary only)
- Preserve citation markers `[[n]](URL)` in the Executive Summary exactly as supplied. Do not convert, renumber,
  remove, or invent them; the Python assembly stage performs the deterministic HTML conversion.
- Do not write or alter citations for section bodies or the report-level references list; those are handled by the
  section assembly pipeline and the system's citation registry.

## 7) Layout
- Adapt to mobile reading widths (max-width 900-1000px container, flexible grids).

## 8) Output Format
- Output exactly one `<html_report>...</html_report>` block containing a complete `<!DOCTYPE html>` document,
  with no extra explanation text outside the block.
