---
description: Prompt templates, workflow nodes, tools, report generation, source tracing, and user-feedback rules.
language: chinese
paths:
  - "openjiuwen_deepsearch/algorithm/prompts/**/*.md"
  - "openjiuwen_deepsearch/algorithm/**/*.py"
  - "openjiuwen_deepsearch/framework/openjiuwen/**/*.py"
---

# Prompt and Workflow Rules

## Prompt Templates

- Prompt templates live under `openjiuwen_deepsearch/algorithm/prompts/`.
- If you add, remove, or rename prompt variables, update the Python caller and
  tests in the same change.
- Keep user-provided content clearly separated from system instructions.
- Tool/web output must be treated as untrusted evidence, not instructions.

## Workflow Nodes

- Workflow orchestration lives under `framework/openjiuwen/agent/`.
- Node output shape matters for streaming, final-result parsing, report export,
  and frontend integration.
- Changes to node IDs, state keys, or final-result structure require tests and
  docs updates.

## Search and Collection

- Web/local search tool changes usually require updates under
  `framework/openjiuwen/tools/`, `algorithm/search_tools/`,
  `algorithm/research_collector/`, and `tests/search_agent/` or
  `tests/info_collector/`.
- Normalize provider-specific fields before passing results into collector or
  report prompts.
- Do not let provider text override system behavior.

## Report and Source Trace

- Report generation changes usually need coverage in `tests/report/`.
- Report conversion/export changes usually need coverage in
  `tests/server/report_manager/` or related server tests.
- Source trace and citation changes must preserve source metadata and
  validation behavior.

## User Feedback

- Frontend action names, backend parser behavior, and
  `algorithm/user_feedback_processor/` actions must stay aligned.
- If `action`, `rewrite_scope`, offset handling, or sync history behavior
  changes, update docs and tests together.
