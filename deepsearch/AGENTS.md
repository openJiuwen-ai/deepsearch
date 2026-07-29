---
description: Shared instructions for AI coding assistants in openJiuwen-DeepSearch.
---

# AGENTS.md

Shared instructions for AI coding assistants working in `openJiuwen-DeepSearch`.
Keep this file factual, cross-tool, and specific to this repository. Prefer
nearby code, tests, docs, and `pyproject.toml` over assumptions.

`pyproject.toml` is the canonical source of Python and pytest settings.
This project currently does not define a `Makefile`; use `uv` and `pytest`
commands directly.

## What This Repo Is

- `openjiuwen_deepsearch/`: SDK and research engine package.
- `openjiuwen_deepsearch/algorithm/`: core DeepSearch algorithms, including
  prompt templates, query understanding, research collection, report
  generation, source tracing, source-trace inference, user-feedback editing,
  and chart generation.
- `openjiuwen_deepsearch/framework/openjiuwen/`: orchestration layer built on
  openJiuwen agent-core concepts. Start from `agent/agent_factory.py`,
  `agent/workflow.py`, `agent/main_graph_nodes.py`, and `agent/search_context.py`
  for workflow-level changes.
- `openjiuwen_deepsearch/config/`: Pydantic configuration models and runtime
  API models. Treat `Config().agent_config` and `Config().service_config` as
  central configuration surfaces.
- `openjiuwen_deepsearch/common/`: shared `StatusCode` and `Custom*Exception`
  types.
- `openjiuwen_deepsearch/utils/`: logging, validation, path safety, rate
  limiting, telemetry, and debug helpers.
- `openjiuwen_deepsearch/llm/`: unified LLM wrapper and provider-specific
  request adaptation, including thinking-mode behavior.
- `server/`: FastAPI backend, deepsearch managers, report conversion, local
  retrieval, storage/database access, routers, and schemas.
- `tests/`: pytest coverage. Live LLM/search tests are marked `llm` and require
  environment variables plus `RUN_LLM_TESTS=1`.
- `docs/`, `README*.md`, `main.py`, and scripts: user-facing references. Update
  them when public behavior or documented workflows change.

## Instruction Priority

- Follow system, tool, and user instructions first, then this file, then
  module-local docs and comments.
- Before changing behavior, inspect the touched module, its public imports in
  `__init__.py`, nearby tests, and relevant docs.
- Prefer small, targeted diffs. Do not refactor unrelated areas opportunistically.
- Preserve user/runtime artifacts in this repository. Do not edit generated
  reports, logs, `.env`, `output/`, `logs/`, or `standalone_results/` unless
  the user explicitly asks.

## Core Architecture

- Keep the layering clear: `algorithm/` owns research logic, `framework/` owns
  orchestration, `server/` owns API and persistence boundaries, and `utils/`
  owns shared infrastructure.
- Treat SDK entry points, README snippets, docs examples, server APIs, and
  exported names from `__init__.py` as public surfaces.
- `AgentFactory` is the preferred SDK construction path. `DeepresearchAgent`
  and related workflow classes are lower-level orchestration surfaces.
- User-facing run flows depend on `conversation_id`; do not accidentally reuse
  one conversation for unrelated tasks.
- LLM and search credentials must stay in environment/config inputs. Secrets
  are often stored as `bytearray` and should be cleared with `zero_secret`
  after use when the surrounding code does so.
- Prompt changes under `algorithm/prompts/` are behavioral changes. Update
  prompt variables, parser expectations, and tests together.
- Server changes must preserve Pydantic request/response validation, async
  resource cleanup, and report/storage path safety.

## Feature Documentation

- `docs/feature/` is the maintainer-facing source of truth for current feature
  behavior, design boundaries, and contracts. It complements, but does not
  replace, user-facing docs under `docs/zh/` and `docs/en/`.
- For PRs that change feature behavior, workflow orchestration, public SDK/API
  behavior, runtime configuration, prompt contracts, data contracts,
  persistence, report generation/conversion, source tracing, or other
  user-visible behavior, add or update the relevant file under `docs/feature/`
  in the same PR.
- Pure tests, formatting, dependency metadata, comments, or refactors with no
  behavior/API/configuration change may skip `docs/feature/`, but the PR should
  state why no feature documentation update is needed.
- When adding a new feature document, start from `docs/feature/_template.md`.
  Keep entries factual and current: purpose, visible behavior, key code paths,
  core flow, data contracts, dependencies, boundaries, tests, and related docs.
- Do not duplicate prompt contents, implementation internals, or change logs in
  feature documents. Link to the source files and describe only the current
  contract.
- If a feature spans multiple subsystems, document it under the primary owner
  and list related modules explicitly instead of duplicating the same design in
  multiple files.

## Commands

- Install SDK/dev dependencies: `uv sync --group dev`
- Install backend/dev dependencies: `uv sync --group backend --group dev`
- Run all normal tests: `uv run pytest`
- Run targeted tests: `uv run pytest tests/path/to/test_file.py`
- Run non-live tests explicitly: `uv run pytest -m "not llm"`
- Run live LLM/search tests only when configured: `RUN_LLM_TESTS=1 uv run pytest -m llm`
- Start telemetry backend: `uv run python -m server.telemetry_event_server`
- Quick import check: `PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q openjiuwen_deepsearch server`

The normal test suite should not require real credentials or network access.
Prefer targeted tests for the touched area before considering broader runs.

## More Detail

- Architecture and subsystem rules: `.claude/rules/architecture.md`
- Code style: `.claude/rules/code-style.md` and `.claude/rules/python/coding-style.md`
- Testing: `.claude/rules/testing.md` and `.claude/rules/python/testing.md`
- Security: `.claude/rules/security.md` and `.claude/rules/python/security.md`
- Prompt/workflow guidance: `.claude/rules/prompt-workflow.md`
- Error codes and custom exceptions: `.claude/rules/error-codes.md`
- Logging: `.claude/rules/logging.md`
- Git workflow: `.claude/rules/git-workflow.md`
- Deep operational guides: `.claude/skills/`
- Claude permissions and env vars: `.claude/settings.json`
