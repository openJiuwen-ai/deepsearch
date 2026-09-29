# `openjiuwen_deepsearch` Directory Structure

This document describes the current stable module boundaries and key entry points in `deepsearch/openjiuwen_deepsearch/`. It is for extension and maintenance work: the tree is organized by responsibility and does not enumerate cache directories or volatile internal files.

## Overview

```text
openjiuwen_deepsearch/
├── algorithm/                  # Research, search, writing, and tracing algorithms
├── common/                     # Exceptions, status codes, and shared definitions
├── config/                     # Pydantic configuration and runtime-API models
├── framework/openjiuwen/       # Workflow orchestration, nodes, tools, and LLM adapters
├── llm/                        # Unified LLM invocation layer
└── utils/                      # Security, logging, rate limiting, validation, and constants
```

## `algorithm/`: domain algorithms

```text
algorithm/
├── brief_report/               # Brief reports, material merging, and HTML output
├── chart_generation/           # Chart generation and sandbox assets
├── prompts/                    # Prompt templates
├── query_understanding/        # Intent, materials, outlines, planning, clarification
├── report/                     # Subreports/reports, evidence, visualization
├── report_export/, report_style/, report_template/
├── research_collector/         # Collection, evidence fusion, webpage enrichment
├── search_agent/, search_index/, search_nodes/, search_tools/
├── source_trace/, source_tracer_infer/
└── user_feedback_processor/    # Post-report local edits and supplementary search
```

The primary `query_understanding/` entry points are `intent_recognition.py`, `material_processing.py`, `outline_mode_router.py`, `interpreter.py`, `outliner.py`, and `planner.py`. This layer produces user-material constraints and section bindings that are consumed by collection and writing.

## `framework/openjiuwen/`: runtime orchestration

```text
framework/openjiuwen/
├── agent/
│   ├── workflow.py             # Streaming Agent entry point and workflow assembly
│   ├── main_graph_nodes.py     # Main-graph nodes
│   ├── brief_nodes.py          # Brief-specific nodes
│   ├── metadata_injectors.py   # Request metadata injectors
│   ├── search_context.py       # Workflow state models
│   ├── collector_graph/        # Collection graph, evidence ledger, webpage enrichment
│   └── reasoning_writing_graph/# Section reasoning/writing graphs
├── core/workflow_agent/        # WorkflowAgent and controller integration
├── llm/                        # Workflow LLM factories and adapters
└── tools/
    ├── fetch_api/              # Web fetch providers, including Jina
    ├── runtime_api/            # Runtime HTTP-tool construction and invocation
    └── search_api/             # Web, local, and scholarly search providers
```

`search_api/` includes `agc_ainetworking`, `harness_web_search`, `jina`, `petal`, `serper`, `tavily`, `xunfei`, and `scholarly_search/` (PubMed, arXiv, Semantic Scholar, and full-text retrieval). External and local adapters are under `framework/openjiuwen/tools/search_api/`: `external_tool/`, `local_search_api/`, and `native_local_search_api/`.

## Configuration and utilities

```text
config/
├── config.py                  # AgentConfig, ServiceConfig, provider configuration
├── runtime_api_models.py      # Pydantic models for runtime HTTP tools
├── method.py                  # Execution-method enums
└── search_mode.py             # Search-mode enums

utils/
├── common_utils/              # Common, text, URL, and security helpers
├── constants_utils/           # Node, session, and search-engine constants
├── debug_utils/               # Debugging, visualization, result export
├── log_utils/                 # Structured logs, metrics, interface logs
├── rate_limiter_utils/        # QPS rate limiting
└── validation_utils/          # Field and request validation
```

## Main execution path and development entry points

```text
workflow.py → main_graph_nodes.py / brief_nodes.py
→ query_understanding → collector_graph + research_collector
→ reasoning_writing_graph + report → source_trace / source_tracer_infer
→ streaming output or a HITL waiting event
```

- Workflow nodes: `framework/openjiuwen/agent/`.
- Report algorithms and prompts: `algorithm/report/` and `algorithm/prompts/`.
- Search or fetch providers: `framework/openjiuwen/tools/search_api/` or `fetch_api/`; also update `config/config.py`.
- State contracts: `framework/openjiuwen/agent/search_context.py`; also update the API reference.
