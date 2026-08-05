**Read this in:** English | [简体中文](./README_zh.md)

# 🔍 What is openJiuwen-CodeSearch?

**openJiuwen-CodeSearch** is an agentic retrieval engine for code repositories.
Given a problem statement — a GitHub issue, a bug report, a feature request — it
returns the files and line ranges you need to look at. Built on **openJiuwen
agent-core**, a retrieval agent autonomously inspects the repository map, runs
multi-strategy searches, expands context, curates findings, and submits its
conclusion — supplying precise code context for fault localization, code Q&A,
and automated repair pipelines.

## Use cases

- **Fault localization**: turn an issue into the exact functions and lines that
  need changing — the first stage of an issue-to-patch pipeline.
- **Context supply for code Q&A**: answer "where is this implemented?" or
  "where does this error come from?" with line-level evidence.
- **Large repository navigation**: trade a natural-language description for
  relevant code slices in an unfamiliar codebase.

## Core capabilities

- **Agentic multi-turn retrieval**
    + The agent decides its own next move: view the repository map, issue
      multi-strategy searches, expand line ranges, curate snippet memory, and
      submit when confident.
    + Two-model design: a capable model drives search decisions while a
      lightweight model extracts relevant lines, keeping cost under control.

- **Code-aware hybrid indexing**
    + Syntax-aware chunking along function and class boundaries, so every
      chunk is a complete semantic unit (**Python only for now**).
    + Dual sparse retrieval: token BM25 for semantic keywords, character
      trigram BM25 for exact substrings such as `data.sum()` or stack traces;
      optional dense-vector hybrid search.
    + Incremental indexing: content-hash deduplication lets multiple revisions
      of a repository share the index of unchanged files.

- **Two equivalent execution engines**
    + Workflow-graph engine (default): the retrieval loop runs as an openJiuwen
      workflow graph with node-level observability.
    + Plain-loop engine: a framework-free fallback.
    + Both share one implementation of the loop stages; tests lock their
      outputs to be byte-identical.

- **Service-ready engineering**
    + Multiple delivery forms: SDK, CLI, HTTP service and container image.
    + Run isolation: per-request context, safe for concurrent use in one process.
    + Multi-product coexistence: collections are namespaced by product prefix
      and schema version, so a single Milvus instance can be shared.
    + Timeouts, bounded concurrency, lifecycle management and token accounting.

## Architecture

```
┌──────────── Indexing (offline) ─────────────┐
│ repository → syntax-aware chunking          │
│           → Milvus dual sparse indexes      │
│ incremental: content-hash dedup across revs │
└─────────────────────────────────────────────┘
┌──────────── Retrieval (online) ─────────────┐
│ problem statement → retrieval agent         │
│   (decision model · filter model · snippet  │
│    memory · five tools) → files + lines     │
└─────────────────────────────────────────────┘
```

Layering (dependencies flow one way): `domain ← config ← algorithm ← framework
← api`, with shared search capabilities factored into `openjiuwen-search-base`.
See the [developer guide](docs/en/4.Developer%20Guide/README.md).

# 📦 Installation

Three deployment options: **local source**, **Docker image** (build yourself),
and **official wheels** (download `openjiuwen-search-base` +
`openjiuwen-codesearch` from the release URL).

Start with the [Quick Guide](docs/en/2.Installation%20Guide/Quick%20Guide.md);
shared env / Milvus / **local repo path** / security notes:
[Installation Guide](docs/en/2.Installation%20Guide/README.md).

Source install example (install sibling `base` together):

```sh
python3 -m venv .venv && .venv/bin/pip install -e ../base -e '.[dev,milvus,llm,server]'
```

Indexing needs a **local directory** (clone remotes first); search uses the
collection name you chose at index time:

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
codesearch index --repo /data/repos/agent-core --collection agent_core
codesearch search --collection agent_core --query "..."
```

Run as an HTTP service:

```sh
codesearch-server
```

Listens on `0.0.0.0:8100`. The service has **no built-in authentication**;
`/api/v1/index` only accepts paths under `CODESEARCH_INDEX_ROOTS` (returns 403
when unset — expected). Indexing currently supports **Python (`.py`) only**.
Full steps: [Installation Guide](docs/en/2.Installation%20Guide/README.md).

# 🚀 Quick start

Index a local repository, then search it in natural language:

```sh
codesearch index --repo /path/to/your/repo --collection my_repo
```

```sh
export CODESEARCH_LLM_API_KEY="your-key"
export CODESEARCH_LLM_BASE_URL="https://api.openai.com/v1"   # any OpenAI-compatible endpoint
# optional: override defaults (main=openai/gpt-5, filter=openai/gpt-5-mini)
# export CODESEARCH_LLM_MODEL="openai/gpt-5"
# export CODESEARCH_FILTER_LLM_MODEL="openai/gpt-5-mini"
codesearch search --collection my_repo --query "TypeError when calling foo() with empty list"
```

Search uses two models: **main** (multi-turn decisions) defaults to `openai/gpt-5`, **filter** (line extraction) defaults to `openai/gpt-5-mini`. When pointing at another endpoint, set model names your provider actually supports. Python API and full options: [Quick Start](docs/en/3.Quick%20Start/3.Quick%20Start.md).

# 📊 Benchmarking

Retrieval quality can be measured on [ContextBench](docs/en/3.Quick%20Start/3.Quick%20Start.md),
a benchmark of real repository issues with annotated ground-truth context.
The dataset is pulled in as a git submodule (see repo-root `.gitmodules`,
path `codesearch/third_party/contextbench`):

```sh
# from the monorepo root
git submodule update --init --recursive
```

```sh
# from codesearch/
pip install -e '.[bench,milvus,llm]'   # [bench]=I/O+scoring; milvus/llm for retrieval
python -m benchmarks.contextbench.runner --num-instances 32
```

Predictions and auto-scored metrics go to `./results/`. Use the product
`[bench]` extra (pandas/pyarrow + tree-sitter*); do not substitute upstream
`requirements.txt`. Details:
[Quick Start](docs/en/3.Quick%20Start/3.Quick%20Start.md#benchmarking).

# 💻 Developer guide

Layered architecture, extension points (new languages, retrieval backends,
agent tools), test tiers and engineering conventions:
[Developer Guide](docs/en/4.Developer%20Guide/README.md) and [AGENTS.md](AGENTS.md).
Feature design documents live in [docs/feature](docs/feature/).

# ❓ FAQ

See the [FAQ](docs/en/5.FAQ/README.md).

# ⚖️ License

Released under the [Apache License 2.0](LICENSE).

# 🤝 Contributing

Issues and pull requests are welcome. Before submitting code, please read the
layering and testing conventions in [AGENTS.md](AGENTS.md) and make sure the
unit tests pass:

```sh
pytest tests/unit -W ignore
```
