# Installation Guide

> Choose a method in the [Quick Guide](./Quick%20Guide.md) first.

## Deployment options

| Option | Use case | Doc |
|---|---|---|
| [Source](./Source%20Install.md) | Development | Editable `base` + `codesearch` |
| [Docker](./Docker%20Install.md) | Isolated delivery | Build from Dockerfile (includes base) |
| [Wheel](./Wheel%20Install.md) | Production | Download **both** official wheels |
| HTTP service | All of the above | `codesearch-server` — see below |

## Repository layout

```text
<repo_root>/
├── base/           # openjiuwen-search-base
└── codesearch/     # openjiuwen-codesearch
```

- **Source**: `pip/uv install -e ../base -e '.[...]'` from `codesearch/`.
- **Docker**: build context is `<repo_root>`; Dockerfile installs both packages.
- **Wheel**: release ships `openjiuwen_search_base-*.whl` and
  `openjiuwen_codesearch-*.whl`.

## Target repository (local path)

Indexing reads a **local directory** visible to the process — not a git URL.

| Step | Parameter | Meaning |
|---|---|---|
| Prepare | (`git clone`) | Clone remotes onto the host (or mount into the container) |
| Index | `--repo` / `repo_path` | Local path to the repository root |
| Index | `--collection` / `collection` | Milvus collection **name you choose** (e.g. `agent_core`) |
| Search | `--collection` / `collection` | Collection name only — **no repo path** |

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
codesearch index --repo /data/repos/agent-core --collection agent_core
codesearch search --collection agent_core --query "..."
```

The product does **not** fetch remote repositories for you. Names like
`agent_core` in examples are **collection labels**, not repository URLs.

## Requirements

| Item | Requirement | Notes |
|---|---|---|
| Python | >= 3.11 | |
| Milvus | >= 2.5 (2.6.x recommended) | Indexing and retrieval; BM25 Function needs 2.5+ |
| LLM API key | `CODESEARCH_LLM_API_KEY` + `CODESEARCH_LLM_BASE_URL` (OpenAI-compatible) | Retrieval only; sparse indexing needs **no** key |

> **Language scope**: the syntax chunker supports **Python (`.py`) only**.
> Indexing other languages yields 0 files — expected.

## Optional dependency groups

| Group | Contents | When needed |
|---|---|---|
| `milvus` | pymilvus | Real repositories |
| `server` | fastapi, uvicorn, pydantic-settings | HTTP service |
| `llm` | openjiuwen | Workflow engine and model calls |
| `embed` | aiohttp | Dense-vector mode |
| `bench` | pandas, pyarrow, tree-sitter* (<3.12: languages; ≥3.12: language-pack) | ContextBench eval; use this extra, not upstream `requirements.txt` |
| `retropus` | tree-sitter*, igittigitt, bm25s, numpy | Retropus engine (in-process KG + BM25, no Milvus) |
| `dev` | pytest | Development |

The server ships inside the package (`openjiuwen_codesearch/server/`); after
installing `[server]`, start with `codesearch-server`.

## HTTP service

```sh
codesearch-server
```

Default `0.0.0.0:8100`; docs at `/docs`, health at `/api/health`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health |
| `/api/v1/search` | POST | Synchronous search; optional `engine` (default `auto`; use `"retropus"` explicitly) |
| `/api/v1/index` | POST | Indexing job; optional `engine` (same); **403 if `CODESEARCH_INDEX_ROOTS` unset** |
| `/api/v1/jobs/{job_id}` | GET | Job status |

### Security boundary

> The service has **no authentication**. `/api/v1/index` reads local directories
> and `/api/v1/search` returns file contents. Therefore:
>
> 1. Set `CODESEARCH_INDEX_ROOTS` (path whitelist, `:`-separated);
> 2. **Unset → indexing returns 403** — a safety default, not a broken install;
> 3. Startup logs a **WARNING** when the whitelist is empty;
> 4. Deploy on a trusted network or behind an access-controlled gateway.

## Environment variables

See `.env.example`. Prefer `.env`; fall back to process env when needed:

1. **`.env` file (wins)**: `cp .env.example .env`, place it in the process cwd (or up to 4 parents). Keys in the file **override** same-named `export` values;
2. **Process env** (when no `.env` is found, or a key is absent): `export` / Docker `-e` / orchestrator injection.

SDK/CLI reads via `CodeSearchConfig.from_env()`; the HTTP server also uses `CODESEARCH_`-prefixed listen settings.

| Variable | Default | Description |
|---|---|---|
| `CODESEARCH_LLM_API_KEY` | empty | LLM API key (retrieval) |
| `CODESEARCH_LLM_BASE_URL` | empty | OpenAI-compatible endpoint (required for search, e.g. `https://api.openai.com/v1`) |
| `CODESEARCH_LLM_MODEL` | `openai/gpt-5` | **main** model (multi-turn search decisions); omit to use this default |
| `CODESEARCH_FILTER_LLM_MODEL` | `openai/gpt-5-mini` | **filter** model (line extraction); omit to use this default |
| `MILVUS_HOST` | `localhost` | Vector store host |
| `MILVUS_PORT` | `19530` | Vector store port |
| `MILVUS_TOKEN` | empty | Credentials |
| `CODESEARCH_HOST` | `0.0.0.0` | Bind address |
| `CODESEARCH_PORT` | `8100` | Port |
| `CODESEARCH_LOG_LEVEL` | `INFO` | Log level |
| `CODESEARCH_INDEX_ROOTS` | empty | Index whitelist; empty → index API 403 |

You can also build a `CodeSearchConfig` in code (field names match deepsearch: `model_name` / `base_url` / `api_key`). Search always uses **main + filter**; with only key/base_url set, model names take the table defaults — change `CODESEARCH_LLM_MODEL` / `CODESEARCH_FILTER_LLM_MODEL` (or `model_name` in code) when switching endpoints.

## Milvus

Collections are named `cs_{name}__{schema_version}`:

```sh
curl -sf http://localhost:9091/healthz && echo " reusable"
```

Dedicated instance: use the upstream `standalone_embed.sh` script; pin the
image tag (e.g. `milvusdb/milvus:v2.6.18`).

## Verify

```sh
pytest tests/unit -W ignore
pytest -m e2e -W ignore
```
