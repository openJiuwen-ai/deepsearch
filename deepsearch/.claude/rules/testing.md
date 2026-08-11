---
description: Pytest locations, markers, live-test boundaries, and verification commands for DeepSearch.
language: chinese
paths:
  - "tests/**/*.py"
alwaysApply: false
---

# Testing Rules

## Test Layout

- Mirror the source area where practical:
  - `algorithm/query_understanding/` -> `tests/algorithm/query_understanding/`
  - `algorithm/report/` -> `tests/report/`
  - `algorithm/source_trace/` -> `tests/source_tracer/`
  - `algorithm/user_feedback_processor/` -> `tests/user_feedback_processor/`
  - `server/` -> `tests/server/`
  - `utils/` -> `tests/utils/`
- Use existing fixtures in `tests/conftest.py` or the nearest subsystem
  `conftest.py` before adding new global fixtures.

## Markers

- `unit`: fast tests without external services.
- `integration`: orchestration tests with mocked runner/workflows.
- `llm`: real LLM/search tests; require credentials and `RUN_LLM_TESTS=1`.

Normal development should avoid real credentials and network calls. Mark live
tests with `llm` and keep them opt-in.

## Commands

- Run all normal tests: `uv run pytest`
- Run targeted tests: `uv run pytest tests/path/to/test_file.py`
- Run non-live tests explicitly: `uv run pytest -m "not llm"`
- Run live tests only when configured: `RUN_LLM_TESTS=1 uv run pytest -m llm`

## Test Data and Artifacts

- Prefer `tmp_path` for files, generated reports, converted documents, and log
  output.
- Do not require `.env` or real API keys in unit tests.
- Use mock defaults or monkeypatch runtime config.
- Keep assertions focused on behavior and public output shape, not incidental
  log wording unless the test is specifically about logging.
