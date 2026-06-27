---
description: Logging conventions for DeepSearch LogManager, session context, truncation, and sensitive output.
language: chinese
paths:
  - "openjiuwen_deepsearch/**/*.py"
  - "server/**/*.py"
---

# Logging Rules

## Project Logging

- Application setup should go through
  `openjiuwen_deepsearch.utils.log_utils.log_manager.LogManager`.
- Library and server modules should use `logging.getLogger(__name__)`.
- Do not configure root handlers ad hoc in library code. Tests may isolate and
  reset handlers when needed.
- Project log filtering is based on `openjiuwen_deepsearch`, `server`, and
  warning/error records from third-party libraries.

## Message Formatting

- For new or touched logging statements, prefer lazy placeholders instead of
  eager f-strings:
  `logger.info("conversation_id=%s", conversation_id)`.
- Do not perform broad logging-only rewrites unless explicitly requested.
- Use `logger.exception("message")` when stack traces are useful.
- Avoid logging huge report bodies. Use the truncation formatter defaults unless
  a test or trusted export path explicitly needs full content.

## Sensitive Data

- Respect `LogManager.is_sensitive()` checks.
- Do not log API keys, tokens, authorization headers, raw credentials, or full
  provider configs.
- Use existing anonymization helpers where available, and clear mutable secrets
  with `zero_secret` after passing them to tools/models when the surrounding
  pattern does so.
- Error messages returned to API callers should not include secrets or internal
  filesystem details.

## Session Context

- Use `session_id_ctx` from `log_common.py` for per-session log context.
- Reset context variable tokens in `finally` blocks.
- Do not store session context in globals that can leak across async tasks.

## Log Paths

- Log directories must be validated through `LogManager` or path-safety helpers.
- Keep runtime logs under approved output roots.
- Do not create new log files in the repository root during tests.
