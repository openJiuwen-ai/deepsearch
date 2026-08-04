# Wheel Install

For production. Download **both** official wheels (no source tree):

| Package | Role |
|---|---|
| `openjiuwen-search-base` | Shared search building blocks (required) |
| `openjiuwen-codesearch` | Product package (CLI + HTTP server) |

Replace `<WHL_BASE_URL>` with the URL from the release announcement.

## Install

```sh
python3 -m venv .venv && source .venv/bin/activate
export WHL_BASE_URL="https://<official-release-url>"

pip install \
  "${WHL_BASE_URL}/openjiuwen_search_base-0.2.0-py3-none-any.whl" \
  "${WHL_BASE_URL}/openjiuwen_codesearch-0.2.0-py3-none-any.whl[milvus,llm,server]"
```

With uv, add `--prerelease=allow` (openJiuwen pins `a2a-sdk==1.0.0a0`).

## Run

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core

export OPENROUTER_API_KEY="your-key"
export MILVUS_HOST=localhost MILVUS_PORT=19530
export CODESEARCH_INDEX_ROOTS="/data/repos"
codesearch index --repo /data/repos/agent-core --collection agent_core
codesearch-server
```

> Empty `CODESEARCH_INDEX_ROOTS` → `POST /api/v1/index` returns **403** by
> design. Startup logs a WARNING. No built-in auth — trusted network / gateway.
> Remotes must be cloned locally before indexing.
