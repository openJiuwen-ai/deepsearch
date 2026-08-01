# Installation Guide

## Deployment options

| Option | Use case |
|---|---|
| Local source | Development and debugging |
| Wheel package | Production installation |
| Docker image | Containerized delivery (starts in service mode by default) |
| HTTP service | Expose retrieval over HTTP |

## Requirements

| Item | Requirement | Notes |
|---|---|---|
| Python | >= 3.11 | |
| Milvus | >= 2.5 (2.6.x recommended) | Required for both indexing and retrieval; full-text search needs the BM25 Function introduced in 2.5 |
| LLM API key | `OPENAI_API_KEY` + `OPENAI_BASE_URL` (default `https://openrouter.ai/api/v1`) | Retrieval only; the default sparse indexing mode needs **no** key |

## Option 1: local source

This package depends on `openjiuwen-search-base` from the same repository.

```sh
uv venv .venv && uv pip install -e ../base -e '.[dev,milvus,llm]'
```

```sh
python3 -m venv .venv && .venv/bin/pip install -e ../base -e '.[dev,milvus,llm]'
```

Optional dependency groups:

| Group | Contents | When needed |
|---|---|---|
| `milvus` | pymilvus | Indexing and retrieving real repositories |
| `server` | fastapi, uvicorn, pydantic-settings | Running as an HTTP service |
| `llm` | openjiuwen | Workflow-graph engine and real model calls |
| `embed` | aiohttp | Dense-vector mode |
| `bench` | pandas, pyarrow | Running benchmarks |
| `dev` | pytest | Development and testing |

The core package only requires pydantic; unit tests and the in-memory retriever
run without any group installed.

> If another openJiuwen-based product on the same machine pins a different
> framework version, install them into separate virtual environments or
> containers — a Python environment can hold only one version of a distribution.
> Vector-store coexistence is unaffected (see below).

## Option 2: wheel

```sh
python -m build && pip install dist/openjiuwen_codesearch-*.whl
```

The wheel ships the library, the `codesearch` CLI and the HTTP service. After
installing it, start the service with `codesearch-server` — no source tree
required.

> Installing the `llm` extra with `uv pip install` outside the source tree fails
> with a pre-release error, because openJiuwen pins `a2a-sdk==1.0.0a0`. Add
> `--prerelease=allow`, or use `pip`, which accepts pre-releases that a
> specifier pins exactly. Installing from source is unaffected: `[tool.uv]` in
> `pyproject.toml` already allows it.

## Option 3: Docker

The build context must be the repository root so that the base package is
included:

```sh
docker build -f codesearch/docker/Dockerfile -t openjiuwen-codesearch:0.2.0 .
```

```sh
docker run --rm -e OPENAI_API_KEY -e OPENAI_BASE_URL -e MILVUS_HOST=host.docker.internal \
  -v /path/to/repo:/repo -v $(pwd)/output:/app/output \
  openjiuwen-codesearch:0.2.0 index --repo /repo --collection demo
```

## Option 4: HTTP service

```sh
pip install -e '.[milvus,llm,server]'
codesearch-server          # or: python start_backend.py, from a source checkout
```

Listens on `0.0.0.0:8100` by default; API docs at `/docs`, health at `/api/health`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check |
| `/api/v1/search` | POST | Synchronous search returning files and line ranges |
| `/api/v1/index` | POST | Submit an indexing job (long running), returns `job_id`. **Requires `CODESEARCH_INDEX_ROOTS`; returns 403 otherwise** |
| `/api/v1/jobs/{job_id}` | GET | Query indexing job status |

Server settings come from `CODESEARCH_`-prefixed environment variables. The
server ships inside the package (`openjiuwen_codesearch/server/`), so source,
wheel and image deployments can all run it.

## Environment variables

All variables are read when `CodeSearchConfig.from_env()` is called. A template
is provided in `.env.example`.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | empty | LLM API key (required for retrieval) |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible API base URL |
| `MILVUS_HOST` | `localhost` | Vector store host |
| `MILVUS_PORT` | `19530` | Vector store port |
| `MILVUS_TOKEN` | empty | Vector store credentials (`user:password` or API token) |
| `CODESEARCH_HOST` | `0.0.0.0` | Service bind address (service mode only) |
| `CODESEARCH_PORT` | `8100` | Service port (service mode only) |
| `CODESEARCH_LOG_LEVEL` | `INFO` | Service log level (service mode only) |
| `CODESEARCH_INDEX_ROOTS` | empty | **Whitelist of directories that may be indexed** (`:`-separated). Empty means `/api/v1/index` returns 403 |

> **Security boundary of the service**: the service ships without
> authentication, `/api/v1/index` reads directories on the server host and
> `/api/v1/search` returns file contents. Always restrict the indexable scope
> with `CODESEARCH_INDEX_ROOTS` (indexing is refused when it is unset), and run
> the service on a trusted network or behind an access-controlled gateway.

Alternatively, construct `CodeSearchConfig` directly and skip environment
variables entirely.

## Milvus deployment

### Sharing one instance with other products (default)

CodeSearch names its collections `cs_{name}__{schema_version}` and uses a
dedicated connection alias, touching only its own namespace. An existing Milvus
instance can therefore be reused as is:

```sh
curl -sf http://localhost:9091/healthz && echo " instance is reusable"
```

> The `cs_` prefix is reserved for CodeSearch — do not name other collections
> `cs_*__v*`. For stronger isolation, use `MilvusConfig.database_name`
> (Milvus 2.2+ databases).

### Running a dedicated instance

```sh
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start
```

> Pin the image tag in the script to a stable release (for example
> `milvusdb/milvus:v2.6.18`). Use `--milvus-port` or `MILVUS_PORT` for
> non-default ports.

Operational note: Milvus community edition has no per-collection resource quota,
so bulk re-indexing affects query latency of other collections on the same
instance. Schedule bulk jobs off-peak or use a dedicated instance for them.

## Verifying the installation

```sh
pytest tests/unit -W ignore      # no external services required
pytest -m e2e -W ignore          # requires a reachable Milvus instance
```
