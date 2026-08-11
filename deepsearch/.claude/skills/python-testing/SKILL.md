---
name: python-testing
description: DeepSearch pytest guide for targeted tests, fixtures, async mocks, live-test gates, and artifact-safe test data.
---

# Python Testing

Use this when adding or updating DeepSearch tests.

## Workflow

1. Locate nearby tests for the touched source.
2. Add the smallest test that captures the behavior.
3. Mock LLM/search/storage/network boundaries by default.
4. Run targeted pytest before broad tests.

## Commands

```bash
uv run pytest tests/path/to/test_file.py
uv run pytest -m "not llm"
RUN_LLM_TESTS=1 uv run pytest -m llm
```

Run live tests only when explicitly requested and credentials are configured.

## Fixtures

- Prefer the nearest `conftest.py`.
- Use `tmp_path` for generated reports, markdown/html/docx files, logs, and databases.
- Use `monkeypatch` for environment variables and runtime config.
- Use `AsyncMock` for async LLM/search calls.

## Async Tests

Pytest uses strict asyncio mode. Mark async tests explicitly and ensure
background tasks are awaited or cancelled.

```python
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_node_uses_llm(monkeypatch):
    fake_call = AsyncMock(return_value={"result": "ok"})
    monkeypatch.setattr("module.path.call_llm", fake_call)

    result = await run_node()

    assert result["result"] == "ok"
    fake_call.assert_awaited_once()
```

## Live-Test Boundary

- Mark real LLM/search tests with `@pytest.mark.llm`.
- Skip unless `RUN_LLM_TESTS=1`.
- Never make live credentials required for normal `uv run pytest`.
