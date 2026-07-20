# `/reports/convert`

## Overview

`/reports/convert` converts a DeepSearch workflow `final_result` payload into a report export bundle. Under the default router registration the full endpoint is `/api/v1/agent/deepsearch/reports/convert`.

Supported `convert_type` values are `html` and `docx`. `convert_content` is a base64-encoded ZIP bundle. HTML can optionally request LLM-generated CSS styling.

## Request Body

```json
{
  "final_result": {
    "response_content": "# Report title",
    "infer_messages": [],
    "chart_messages": [],
    "warning_info": "",
    "exception_info": ""
  },
  "convert_type": "html",
  "enable_html_styling": false
}
```

- `final_result.response_content`: required Markdown report body.
- `final_result.infer_messages`: optional source-tracing assets with `id` and `html_base64`. Report links using `#inference:<id>` become relative links to standalone HTML files in the bundle.
- `final_result.chart_messages`: optional VLM PNG assets with `chart_id`, `base64`, and optional `chart_title`. `(#insertChart:<chart_id>)` placeholders become image references.
- `convert_type`: `html` or `docx`.
- `enable_html_styling`: optional and defaults to `false`; it applies only to HTML.
- `llm_config`: required only when HTML styling is enabled; it may be a direct model configuration or a category configuration containing `general`. Category configurations use only `general`.

## Response Body

```json
{
  "code": 200,
  "msg": "success",
  "convert_content": "<base64-zip>",
  "style_applied": false,
  "style_status": "not_requested"
}
```

`style_status` is `not_requested` for ordinary HTML, `not_supported` for DOCX, `applied` after successful styling, or `fallback` when only styling fails.

## HTML Styling

When `convert_type=html` and `enable_html_styling=true`, `/reports/convert` builds semantic baseline HTML and asks the configured LLM for CSS based on the report title, outline, and summary. `llm_config` may be direct or category based; category configurations use only `general`. The LLM does not rewrite Markdown, links, charts, or inference assets.

```json
{
  "convert_type": "html",
  "enable_html_styling": true,
  "llm_config": {"general": {"model_name": "your-model", "api_key": "your-key"}}
}
```

Successful styling returns `style_applied=true` and `style_status=applied`. If CSS generation or injection fails, the endpoint still returns HTTP 200 with readable semantic baseline HTML, `style_applied=false`, and `style_status=fallback`. Missing or invalid `llm_config` returns HTTP 400; base bundle or export failures return HTTP 500. DOCX never initializes or validates the LLM configuration.

## ZIP Layout

```text
report_bundle/
  report.md
  report.html | report.docx
  infer/                       # optional
    inference_<id>.html
  charts/                      # optional
    <chart_id>.png
```

`report.md` is the rewritten intermediate Markdown. Inference HTML remains standalone. VLM PNG files remain in `charts/`; both HTML variants additionally inline their rendered references as Data URIs so `report.html` can be opened after extraction on its own.

## Diagrams, Images, And Formulas

### Mermaid

Mermaid Markdown remains the report contract. Ordinary and styled HTML plus DOCX from `/reports/convert` share deterministic parsing and layout for the chart source produced by this project: vertical `xychart-beta` bar/line charts, horizontal bars marked by `xychart-beta horizontal` or `horizontal: true`, `pie`, and `timeline`.

- HTML emits supported charts as escaped inline SVG.
- DOCX uses Pillow and the repository-bundled `chart_generation/fonts/kt_font.ttf` to produce in-memory PNG data inserted through `BytesIO`; no Mermaid image files are created.
- Engineering-scale normalization and `showDataLabel` keep the same data and layout meaning across HTML and DOCX. Pie legends include names and values; timelines retain their existing note semantics.
- A parse, render, or font-load failure affects only that chart and preserves its original Mermaid source block.
- Unsupported Mermaid types remain source blocks and Mermaid.js is never loaded.

This feature has no external Mermaid command, Node, Chrome, or vector-rendering runtime dependency and needs no related installation or environment variables.

### Math

- HTML retains the MathJax configuration and the jsDelivr MathJax runtime.
- DOCX converts normalized formulas to Word OMML during export.

### VLM Images

- `chart_messages[*].base64` is decoded under the existing PNG contract and retained in `charts/`.
- Main HTML reports embed those PNGs as Data URIs; DOCX continues to insert the bundle-local image.
- No Base64 size, pixel, or image-format limit is added.

## Failures And Fallbacks

- Empty `response_content`, a non-dictionary `final_result`, or invalid inference/VLM base64 data produce the existing validation errors.
- Main-report or ZIP generation failures produce the existing execution errors.
- Mermaid failures are local fallback only and do not create Mermaid CLI debug artifacts.
- Existing request fields, format values, and the ZIP top-level layout remain unchanged; the response adds style status fields.
