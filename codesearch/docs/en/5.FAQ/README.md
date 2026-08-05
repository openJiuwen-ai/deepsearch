# FAQ

## Installation

**Pre-release dependency error during installation?**  
openJiuwen pulls in a pre-release transitive dependency; pass
`--prerelease=allow` when using uv (already enabled under the source tree's
`[tool.uv]`). For wheels see [Wheel Install](../2.Installation%20Guide/Wheel%20Install.md).

**Source vs Docker vs wheel?**  
See the [Quick Guide](../2.Installation%20Guide/Quick%20Guide.md). Source for
editing code; Docker to `docker build` yourself; wheels download **base +
codesearch** from the official URL (no source tree).

**Installed codesearch without base?**  
`openjiuwen-codesearch` depends on `openjiuwen-search-base`. Source needs
`pip install -e ../base`; wheels need both files; Docker images already include
base.

**Can it share a virtual environment with another openJiuwen-based product?**  
Not when the two pin different framework versions. Use separate environments or
containers. Vector-store coexistence is unaffected.

## Indexing

**Can I pass a GitHub / gitcode URL directly?**  
No. Only a **local directory** (`--repo` / `repo_path`) is accepted. Clone
first, then index that path. Search uses the `--collection` name chosen at
index time (e.g. `agent_core`), not the git URL.

**Indexing a non-Python repository yields 0 files.**  
The chunker supports **Python (`.py`) only** — product scope, not a failure.
Other languages: implement `Chunker` — see the
[Developer Guide](../4.Developer%20Guide/README.md).

**`POST /api/v1/index` returns 403?**  
`CODESEARCH_INDEX_ROOTS` is unset or `repo_path` is outside the whitelist.
Refusing indexing when unset is the **safety default** (no auth; index reads
local disk). Startup logs a WARNING. Example:

```sh
export CODESEARCH_INDEX_ROOTS="/data/repos"
```

**How do I control disk usage on a large repository?**  
Pilot with `--max-files 200`, then extrapolate. Add `--no-trigram` to skip the
trigram field (~7× source size) at the cost of exact-substring search.

**Does re-indexing the same repository duplicate storage?**  
No. Content-hash dedup; `reused` in the output counts reused files.

**Does indexing need an LLM API key?**  
Not in default sparse mode. Needed only for dense-vector mode.

## Retrieval

**Search returns `index_not_ready`.**  
No data for that collection/revision. Run `index` first; keep `--revision`
consistent (default `local`).

**What do the termination values mean?**

| Value | Meaning |
|---|---|
| `submitted` | Agent submitted its conclusion |
| `stagnated` | No new findings for several turns; stopped early |
| `max_turns` | Turn limit reached |
| `no_tool_call` / `llm_error` | Model stopped or call failed |
| `index_not_ready` | Index unavailable |

**How do I trade off depth against token usage?**  
`max_turns`, `stagnation_rounds`, `search_topk`, `retrieve_topk` in
`SearchAgentConfig`. Results include `total_input_tokens` /
`total_output_tokens`.

**Can I use local or third-party models?**  
Yes. Set `CODESEARCH_LLM_API_KEY` and `CODESEARCH_LLM_BASE_URL` (any OpenAI-compatible
endpoint). Search uses two models: **main** (defaults to `openai/gpt-5`) and
**filter** (defaults to `openai/gpt-5-mini`). Override with
`CODESEARCH_LLM_MODEL` / `CODESEARCH_FILTER_LLM_MODEL`, or build an `LLMSuite` in
code. When switching endpoints, use model names that provider actually supports.
See [Quick Start](../3.Quick%20Start/3.Quick%20Start.md) and
[Installation · Environment variables](../2.Installation%20Guide/README.md#environment-variables).

## Runtime

**Does the service authenticate? How to deploy in production?**  
**No built-in auth.** `/api/v1/index` reads local dirs; `/api/v1/search` returns
file contents. Set `CODESEARCH_INDEX_ROOTS` and place the service on a trusted
network or behind a gateway. See
[Installation · Security](../2.Installation%20Guide/README.md#security-boundary).

**SSL / certificate errors?**  
certifi is used by default; set `LLMConfig.ssl_cert` for self-signed CAs.

**Workflow execution timeout?**  
SDK injects `SearchAgentConfig.time_limit_seconds` (default 900s).

**`docker ps` permission denied on Linux?**  
`sudo usermod -aG docker $USER` then re-login.

**Docker ignores `index` arguments?**  
Default entrypoint is `codesearch-server`. Use `--entrypoint codesearch` —
see [Docker Install](../2.Installation%20Guide/Docker%20Install.md).
