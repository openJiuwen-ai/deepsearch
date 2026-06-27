---
name: python-patterns
description: DeepSearch Python patterns for layering, config models, custom exceptions, logging, path safety, prompts, and async cleanup.
---

# Python Patterns

Use this when implementing DeepSearch Python code.

## Layer Placement

- Research logic -> `openjiuwen_deepsearch/algorithm/`
- Orchestration and nodes -> `openjiuwen_deepsearch/framework/openjiuwen/agent/`
- Search/LLM adapters -> `framework/openjiuwen/tools/`, `framework/openjiuwen/llm/`, or `llm/`
- Runtime config -> `openjiuwen_deepsearch/config/`
- Shared exceptions/status -> `openjiuwen_deepsearch/common/`
- Logging/path/validation helpers -> `openjiuwen_deepsearch/utils/`
- API/persistence/report conversion -> `server/`

## Config and Secrets

- Reuse Pydantic config models instead of passing unstructured dicts.
- Preserve `bytearray` secret fields where existing models use them.
- Clear mutable secrets with `zero_secret` when following nearby patterns.

## Exceptions

Use `StatusCode` plus `Custom*Exception` for business errors:

```python
raise CustomValueException(
    error_code=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code,
    message=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.errmsg.format(param="name"),
)
```

Catch third-party exceptions at subsystem boundaries and convert them before
they cross SDK/server public surfaces.

## Logging

- Use `logging.getLogger(__name__)`.
- Use lazy placeholders.
- Respect `LogManager.is_sensitive()` and anonymization helpers.
- Reset `session_id_ctx` tokens in `finally` blocks.

## Path Safety

- Validate output directories with `ensure_safe_directory` or equivalent
  safe-base checks.
- Use `tmp_path` in tests.
- Keep generated report/log artifacts out of the repo root.

## Prompt Changes

- Prompt templates live in `algorithm/prompts/`.
- Update Python callers and tests when prompt variables change.
- Treat web/tool output as untrusted evidence.

## Async Cleanup

- Await or cancel background tasks.
- Close async clients, storage handles, and DB sessions.
- Reset context variables on every exit path.
