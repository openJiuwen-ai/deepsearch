# Source Install

For developers and contributors. Install sibling `base` and this package in
editable mode.

## Prerequisites

| Item | Requirement |
|---|---|
| Python | >= 3.11 |
| Milvus | >= 2.5 (2.6.x recommended) |
| LLM API key | `OPENAI_API_KEY` (retrieval only; `OPENAI_BASE_URL` defaults to OpenRouter) |

```text
<repo_root>/
├── base/           # openjiuwen-search-base
└── codesearch/     # openjiuwen-codesearch
```

## Install

```sh
cd codesearch
uv venv .venv
uv pip install -e ../base -e '.[milvus,llm,server]'
```

```sh
cd codesearch
python3 -m venv .venv
.venv/bin/pip install -e ../base -e '.[milvus,llm,server]'
```

> You must install `../base`. Installing codesearch alone will fail dependency
> resolution.

See also [Quick Start](../3.Quick%20Start/3.Quick%20Start.md).

> Target code must be a **local directory** (clone remotes first). `--collection`
> is a name you choose; search uses that name only — see
> [Installation overview · Target repository](./README.md#target-repository-local-path).

## Run

```sh
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"   # default; can omit
export MILVUS_HOST=localhost MILVUS_PORT=19530
export CODESEARCH_INDEX_ROOTS="/data/repos"

git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
codesearch index --repo /data/repos/agent-core --collection agent_core
codesearch-server
```

```sh
curl -sf http://127.0.0.1:8100/api/health
```
