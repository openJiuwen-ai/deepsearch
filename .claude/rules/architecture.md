---
description: DeepSearch architecture, public surfaces, subsystem boundaries, and change rules.
language: chinese
paths:
  - "openjiuwen_deepsearch/**/*.py"
  - "server/**/*.py"
---

# Architecture Rules

## Public Surfaces

- Treat README snippets, docs examples, server API schemas, SDK entry points,
  and exports from `__init__.py` as public surfaces.
- Keep public API changes backward-compatible unless the user explicitly asks
  for a breaking change.
- Prefer keyword-only additions for optional parameters.
- Before changing behavior, inspect nearby tests and docs in addition to the
  implementation file.

## Layering

- `algorithm/` owns core research logic: query understanding, collection,
  reporting, source tracing, source-trace inference, user-feedback editing, and
  chart generation.
- `framework/openjiuwen/` owns orchestration: agent factory, workflow entry,
  graph nodes, search context, LLM factory, and search tools.
- `server/` owns FastAPI routers, schemas, persistence, report conversion, and
  backend managers. Do not move API-only concerns into SDK algorithm code.
- `config/` owns runtime and service configuration models.
- `common/` owns `StatusCode` and `Custom*Exception`.
- `utils/` owns shared infrastructure such as logging, security utilities,
  validation, rate limiting, debug output, and telemetry helpers.

## Workflow Entry Points

- Prefer `AgentFactory.create_agent(agent_config)` for user-facing SDK
  construction.
- For workflow changes, start from:
  - `openjiuwen_deepsearch/framework/openjiuwen/agent/agent_factory.py`
  - `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
  - `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
  - `openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`
- Preserve streaming behavior and final-result parsing. Changes to node output
  shape usually require test and docs updates.
- `conversation_id` is a task/session boundary. Do not reuse it for unrelated
  runs or hide collisions in tests.

## Algorithm Subsystems

- Query understanding changes usually touch `algorithm/query_understanding/`,
  prompt templates, and `tests/algorithm/query_understanding/`.
- Research collection changes usually touch `algorithm/research_collector/`,
  collector graph code under `framework/openjiuwen/agent/`, and
  `tests/info_collector/`.
- Report generation and conversion changes usually touch `algorithm/report/`,
  `server/deepsearch/core/manager/report_manager/`, and `tests/report/` or
  `tests/server/report_manager/`.
- Source tracing changes usually touch `algorithm/source_trace/`,
  `algorithm/source_tracer_infer/`, and matching tests.
- User-feedback editing changes usually touch
  `algorithm/user_feedback_processor/`, `framework/openjiuwen/agent/` nodes,
  docs describing frontend actions, and `tests/user_feedback_processor/`.

## Config, Secrets, and State

- `Config().agent_config` is user-configurable runtime input; `service_config`
  contains internal service defaults and switches.
- LLM/search/storage credentials must come from environment or runtime config,
  never hard-coded constants.
- Preserve `bytearray` secret handling and call `zero_secret` when the
  surrounding code uses it.
- Context variables such as `session_id_ctx` and workflow context must be reset
  on all exit paths.

## Server Boundary

- Keep request and response validation in Pydantic schemas under `server/schemas/`.
- Managers under `server/deepsearch/core/manager/` should own backend workflow
  orchestration and persistence coordination.
- Storage, database, and report conversion paths must stay scoped and validated.
- Async resources must be closed on success, failure, and cancellation.

## Module-Local Guidance

- If a subsystem needs more detail, add a local `CLAUDE.md` or `AGENTS.md`
  near that subtree instead of expanding this root rule file too far.
- Keep module-local guidance factual and tied to existing code contracts.
