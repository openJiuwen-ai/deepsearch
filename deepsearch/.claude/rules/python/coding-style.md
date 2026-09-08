---
description: Python-specific conventions for DeepSearch: typing, Pydantic, async patterns, and anti-patterns.
language: chinese
paths:
  - "openjiuwen_deepsearch/**/*.py"
  - "server/**/*.py"
alwaysApply: false
---

# Python Coding Style

Extends `.claude/rules/code-style.md`.

## Type Hints

- Python `>=3.11,<3.14` is required by `pyproject.toml`.
- Use modern built-in generics such as `list[str]` and `dict[str, Any]`.
- Use `Protocol` for structural interfaces when it avoids inheritance coupling.
- Type new public function parameters and return values.

## Pydantic and Config

- Keep request/response models in `server/schemas/`.
- Keep DeepSearch config models in `openjiuwen_deepsearch/config/`.
- Do not add ad hoc dict shapes when a nearby Pydantic model already exists.
- Preserve secret field types such as `bytearray` unless a broader migration is
  explicitly requested.

## Async Patterns

- Use `asyncio.gather` intentionally and handle cancellation where long-running
  tasks are spawned.
- Reset context variable tokens in `finally` blocks.
- Avoid synchronous file/network I/O in async server handlers unless the
  surrounding implementation already accepts it.

## Anti-Patterns

- Mutable default arguments.
- Bare `except:`.
- New broad `except Exception` blocks that swallow errors.
- Logging with f-strings when values may be expensive or sensitive.
- Writing runtime artifacts to the repository root from tests or library code.
- Using bare `float()` or `.strip()` on values derived from LLM JSON output without `safe_float()` / `isinstance` / `str()` guards — LLM may return string numbers, `None`, or unexpected container types that cause `TypeError`/`AttributeError` deep in the pipeline.
