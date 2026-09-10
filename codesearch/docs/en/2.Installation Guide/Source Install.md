# Source Install

For developers and contributors. Install sibling `base` and this package in
editable mode.

## Prerequisites

| Item | Requirement |
|---|---|
| Python | >= 3.11 |
| Milvus | >= 2.5 (2.6.x recommended) |
| LLM API key | `CODESEARCH_LLM_API_KEY` + `CODESEARCH_LLM_BASE_URL` (retrieval only) |

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
> resolution. Add the `retropus` extra only when using `engine=retropus`
> (in-process KG + BM25, no Milvus), e.g. `'.[milvus,llm,server,retropus]'`
> or `'.[dev,llm,retropus]'`. See [optional dependency groups](./README.md#optional-dependency-groups).

See also [Quick Start](../3.Quick%20Start/3.Quick%20Start.md).

> Target code must be a **local directory** (clone remotes first). `--collection`
> is a name you choose; search uses that name only — see
> [Installation overview · Target repository](./README.md#target-repository-local-path).

## Run

```sh
export CODESEARCH_LLM_API_KEY="your-key"
export CODESEARCH_LLM_BASE_URL="https://api.openai.com/v1"   # any OpenAI-compatible endpoint
# optional: defaults main=openai/gpt-5, filter=openai/gpt-5-mini
# export CODESEARCH_LLM_MODEL="openai/gpt-5"
# export CODESEARCH_FILTER_LLM_MODEL="openai/gpt-5-mini"
export MILVUS_HOST=localhost MILVUS_PORT=19530
export CODESEARCH_INDEX_ROOTS="/data/repos"

git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
codesearch index --repo /data/repos/agent-core --collection agent_core
codesearch-server
```

```sh
curl -sf http://127.0.0.1:8100/api/health
```
