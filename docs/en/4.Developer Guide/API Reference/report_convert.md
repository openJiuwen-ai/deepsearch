# `/reports/convert`

## Overview

`/reports/convert` converts a DeepSearch workflow `final_result` payload into a report export bundle.

Under the default backend router registration, the full endpoint path is `/api/v1/agent/deepsearch/reports/convert`.

Supported export formats:

- `html`
- `docx`

The response is a base64-encoded ZIP bundle.

## Request Body

```json
{
  "final_result": {
    "response_content": "# Report Title",
    "infer_messages": [],
    "chart_messages": [],
    "warning_info": "",
    "exception_info": ""
  },
  "convert_type": "html"
}
```

Field notes:

- `final_result.response_content`
  - Markdown report body.
- `final_result.infer_messages`
  - Source-tracing graph resources. To export these resources into the ZIP bundle, each item should include:
    - `id`: Graph identifier used to rewrite `#inference:<id>` links in the report body.
    - `html_base64`: Base64-encoded HTML content that is decoded into a standalone HTML file.
- `final_result.chart_messages`
  - VLM chart resources. To export these resources into the ZIP bundle, each item should include:
    - `chart_id`: Chart identifier used to match `(#insertChart:<chart_id>)` placeholders in the report body.
    - `base64`: Base64-encoded image content that is decoded into an image file.
    - `chart_title`: Optional. When present, it becomes the alt text of the rewritten Markdown image reference.
- `convert_type`
  - Target export format, either `html` or `docx`.

## Response Body

```json
{
  "code": 200,
  "msg": "success",
  "convert_content": "<base64-zip>"
}
```

Field notes:

- `convert_content`
  - Base64-encoded ZIP bundle.

## ZIP Layout

The ZIP bundle always contains `report_bundle/report.md` and the main exported artifact for the requested format. Source-tracing assets, chart assets, and Mermaid debug files appear only when relevant.

```text
report_bundle/
  report.md
  report.html | report.docx
  infer/                       # optional
    inference_0.html
  charts/                      # optional
    chart_0.png
  report_mermaid_0.mmd         # optional, only when Mermaid rendering fails
  report_mermaid_0.error.txt   # optional, only when Mermaid rendering fails with diagnostics
```

Notes:

- `report.md`
  - Intermediate Markdown after inference-link and chart-placeholder rewriting.
- `report.html` or `report.docx`
  - Main exported artifact.
- `infer/`
  - Standalone source-tracing graph HTML resources; present only when exportable `infer_messages` entries exist.
- `charts/`
  - VLM chart resources; present only when exportable `chart_messages` entries exist.
- `report_mermaid_0.mmd` / `report_mermaid_0.error.txt`
  - Mermaid debug artifacts kept for troubleshooting Mermaid source or runtime issues after render failures.

## Diagram Handling

### Mermaid

- For HTML export, Mermaid is rendered offline to SVG through `mmdc` and embedded into the HTML output.
- For DOCX export, Mermaid is rendered offline to PNG through `mmdc` and then converted through pandoc.
- If `mmdc` is unavailable, the export does not fail. The Mermaid source block is preserved in the output instead.

### Math Formulas

- HTML export includes MathJax configuration for LaTeX formulas, but the MathJax runtime is loaded from jsDelivr CDN.
- In offline or intranet-only deployments, HTML formulas may not render unless that CDN URL is reachable or the runtime is provided by a deployment-specific replacement.
- DOCX export converts normalized formulas into Word OMML during export and does not depend on MathJax at viewing time.

### Source-Tracing Graphs

- `infer_messages[*].html_base64` is decoded into standalone HTML files.
- `#inference:<id>` links are rewritten to relative bundle-local HTML links.
- The graphs are preserved as standalone resources rather than inline content in the main document.

### VLM Charts

- `chart_messages[*].base64` is decoded into image files.
- `(#insertChart:<chart_id>)` placeholders are rewritten into Markdown image references pointing to `charts/<chart_id>.png`.

## Export Environment Prerequisites

### Offline Mermaid Rendering

Offline Mermaid rendering depends on `mmdc`, usually installed through npm:

```bash
npm install -g @mermaid-js/mermaid-cli
```

Installing the `mmdc` command alone is not enough. At runtime, `mmdc` also needs a Chrome or `chrome-headless-shell` executable that Puppeteer can launch. The recommended follow-up step is:

```bash
npx puppeteer browsers install chrome-headless-shell
```

If Chrome is already installed locally, or if `chrome-headless-shell` has already been downloaded by Puppeteer, you can explicitly point to the browser executable through an environment variable. Common examples:

```bash
# macOS
export PUPPETEER_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Linux
export PUPPETEER_EXECUTABLE_PATH="/usr/bin/google-chrome"

# Windows PowerShell
$env:PUPPETEER_EXECUTABLE_PATH="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

You can also point to a custom Mermaid CLI executable with:

```bash
export MERMAID_MMDC_PATH=/path/to/mmdc
```

`PUPPETEER_EXECUTABLE_PATH` can also point directly to a Puppeteer-downloaded `chrome-headless-shell` binary. If `mmdc` is installed but still fails with `Could not find Chrome (...)`, check and set this variable first.

If `/reports/convert` is served by a long-running backend process, restart the backend after changing `PUPPETEER_EXECUTABLE_PATH` or `MERMAID_MMDC_PATH`. Existing processes do not pick up updated environment variables automatically.

Do not treat `mmdc --version` as the only success signal. Run a minimal render test as well:

```bash
tmpdir=$(mktemp -d)
cat > "$tmpdir/test.mmd" <<'EOF'
graph TD
A-->B
EOF
mmdc -i "$tmpdir/test.mmd" -o "$tmpdir/test.svg" -b white
```

If `test.svg` is created successfully, the Mermaid offline rendering prerequisites are ready.

### DOCX Export

DOCX export depends on both `pypandoc` and the `pandoc` binary:

- Recommended: preinstall `pandoc` in the deployment environment
- Compatible fallback: if `pandoc` is missing, the program can attempt to download it through `pypandoc`

### Minimal Verification

Before calling `/reports/convert`, it is recommended to verify both Mermaid and Pandoc dependencies.

For Mermaid, you can reuse the minimal `mmdc` render test shown above.

Minimal Pandoc verification:

```bash
pandoc --version
```

If `pandoc` is not preinstalled, the first DOCX export can also trigger an automatic download attempt through `pypandoc`.

## Failure And Fallback Behavior

- The export fails when `response_content` is empty.
- The export fails when `infer_messages` or `chart_messages` contain invalid base64 payloads.
- Mermaid rendering failures do not stop the export. Mermaid falls back to source code blocks in the output.
- If `mmdc` is installed but Chrome or `chrome-headless-shell` is still missing, a common error is `Could not find Chrome (...)`. This means Mermaid CLI is present, but the browser runtime is not ready yet.
- When Mermaid rendering fails, the ZIP bundle may include `.error.txt` and `.mmd` debug artifacts. They preserve the failure details and Mermaid source for troubleshooting.
