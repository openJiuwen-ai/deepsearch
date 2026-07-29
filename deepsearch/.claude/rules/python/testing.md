---
description: Pytest, async tests, fixtures, mocking, and live-test conventions for DeepSearch.
language: chinese
paths:
  - "tests/**/*.py"
alwaysApply: false
---

# Python Testing

Extends `.claude/rules/testing.md`.

## Pytest Configuration

- Pytest configuration lives in `pyproject.toml`.
- `asyncio_mode` is strict; make async tests explicit and deterministic.
- Use existing markers: `unit`, `integration`, and `llm`.

## Fixtures and Mocks

- Prefer local fixtures in the nearest `conftest.py`.
- Use `monkeypatch` for environment variables, config overrides, and provider
  replacement.
- Use `unittest.mock.AsyncMock` for async LLM/search calls.
- Keep real credentials out of tests.

## Live Tests

- Live tests must be marked `llm`.
- Gate live tests with `RUN_LLM_TESTS=1` and required env variables.
- Do not make the normal test suite depend on external services.

## Artifacts

- Use `tmp_path` for generated markdown, html, docx, mmd, logs, databases, and
  report bundles.
- Clean up background tasks and temporary resources.
