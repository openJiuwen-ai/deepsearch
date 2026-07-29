---
description: General code style, imports, async safety, logging, and file organization for DeepSearch.
language: chinese
paths:
  - "openjiuwen_deepsearch/**/*.py"
  - "server/**/*.py"
  - "tests/**/*.py"
alwaysApply: false
---

# Code Style Rules

## Baseline

- Python `>=3.11,<3.14` is required by `pyproject.toml`.
- Follow the style of the surrounding module before introducing new patterns.
- Do not assume a formatter or linter that is not configured in this repo.
- Keep diffs focused on the requested behavior.

## Imports and Types

- Use absolute imports inside `openjiuwen_deepsearch` and `server`.
- Avoid wildcard imports in source code.
- Add type hints for new public APIs and for complex helper functions.
- Keep `__init__.py` focused on public exports; do not hide implementation logic there.

## Async Safety

- Avoid blocking I/O in async paths unless the surrounding module already
  explicitly does so.
- Close async resources on success, failure, and cancellation.
- Preserve context variable reset patterns in workflow/session code.

## Logging

- Use the project logging setup and `logging.getLogger(__name__)`.
- For new or touched logging statements, prefer lazy placeholders:
  `logger.info("value=%s", value)`.
- Do not perform broad logging-only rewrites unless explicitly requested.
- Use `logger.exception("message")` on exception paths that should include a stack trace.
- Do not log API keys, tokens, raw credentials, or sensitive user content.
- Full logging rules: `.claude/rules/logging.md`.

## Runtime Artifacts

- Do not write test reports, generated documents, logs, databases, or other
  runtime artifacts into the repo root from tests. Use `tmp_path` or a scoped
  output directory.
- Do not edit generated report/log artifacts unless explicitly asked.
