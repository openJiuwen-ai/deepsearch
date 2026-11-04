# Developer Guide

## Layered architecture

Dependencies flow in one direction only (right depends on left):

```
[base] ← domain ← config ← algorithm ← framework/openjiuwen ← api
                                ↑ indexing / retrieval
```

| Layer | Responsibility | Constraint |
|---|---|---|
| `openjiuwen-search-base` | Shared search capabilities: LLM adapter, embedding client, Milvus store and expression builders, workflow node template, logging, run registry | Depends on no product package |
| `domain/` | Pure models: snippet, snippet memory, termination, results | Imports nothing else in the package |
| `config/` | pydantic configuration models | Parsed once, read-only at runtime, no global mutable state |
| `algorithm/` | Retrieval algorithms and agent tools | **Must not import `framework/`** |
| `indexing/` `retrieval/` | Chunking, embedding, persistence; retrieval protocol and Milvus implementation | Heavy dependencies use guarded imports |
| `framework/openjiuwen/` | Loop stage functions, workflow graph, run context and isolation | Live objects are injected via the run registry, never stored in workflow state |
| `api/` | Public facade | The only public surface |
| `benchmarks/` | Evaluation adapters | Depends only on the public API |
| `openjiuwen_codesearch/server/` | HTTP service layer (FastAPI): health, search, indexing jobs | Depends only on the public API; ships with the wheel, so wheel installs can serve too |

## Execution engines

`SearchAgentConfig.engine`:

| Value | Meaning |
|---|---|
| `graph` (default) | openJiuwen workflow graph, node-level observability |
| `react` | Plain code loop, no framework dependency |

Both engines share the stage functions in `framework/openjiuwen/steps.py`;
integration tests lock their outputs to be byte-identical. See the
[workflow feature doc](../../feature/framework/codesearch-workflow.md).

## Extension points

| Goal | How |
|---|---|
| Support a new language | Implement the `Chunker` protocol in `indexing/chunkers/base.py` |
| Add a retrieval backend | Implement the `CodeRetriever` protocol in `retrieval/base.py` |
| Integrate another model service | Implement the base package's `LLMClient` protocol |
| Add an agent tool | Add a `ToolSpec` (schema + executor) under `algorithm/search_tools/` and register it |

## Key configuration

| Setting | Default | Description |
|---|---|---|
| `agent.max_turns` | 20 | Maximum retrieval turns |
| `agent.stagnation_rounds` | 3 | Stop early after this many search turns with no new findings |
| `agent.search_topk` | 10 | Snippets returned per search |
| `agent.retrieve_topk` | 20 | Maximum snippets in the final result |
| `agent.filter_concurrency` | 8 | Concurrency cap for line-level filtering |
| `agent.time_limit_seconds` | 900 | Workflow timeout per run |
| `index.enable_trigram` | True | Trigram field toggle (dominant storage cost) |
| `index.max_num_files_per_repo` | None | Cap on files indexed per repository |
| `index.max_file_size_bytes` | 5MB | Files larger than this are skipped |
| `milvus.collection_prefix` | `cs_` | Collection namespace prefix |
| `milvus.schema_version` | `v1` | Index schema version; bump on schema changes |

Secret fields (`llm.*.api_key`, `embed.api_key`, `milvus.token`) are stored as
`bytearray` and decoded only when calling an external service; pass plain
strings when constructing. Use
`openjiuwen_search_base.security.zero_secret` to wipe them in place.

## Testing

```sh
pytest tests/unit -W ignore          # no external dependencies, includes full trace replay
pytest tests/integration -W ignore   # requires openjiuwen (workflow graph) and the server group
pytest -m e2e -W ignore              # requires a reachable Milvus instance
```

Conventions: unit tests must not import openjiuwen or pymilvus. Behavioural
contracts (memory rendering, result ordering, tool message text, line-number
mapping) are locked by dedicated cases — changing them requires updating both
the tests and the feature documentation.

## Engineering conventions

See [AGENTS.md](../../../AGENTS.md). Highlights: all Milvus expressions are
built through the base package's safe builders (never string concatenation);
configuration is injected through models while runtime state lives on the run
context; user-visible changes must be reflected in `docs/`.
