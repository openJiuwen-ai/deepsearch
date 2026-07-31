# FAQ

## Installation

**Pre-release dependency error during installation?**
openJiuwen pulls in a pre-release transitive dependency; pass
`--prerelease=allow` when using uv (already enabled in this package's
`[tool.uv]` section).

**Can it share a virtual environment with another openJiuwen-based product?**
Not when the two pin different framework versions — a Python environment can
hold only one version of a distribution. Use separate environments or
containers. Vector-store coexistence is unaffected.

## Indexing

**Indexing a non-Python repository yields 0 files.**
The current chunker supports Python only. Other languages can be added by
implementing the `Chunker` protocol — see the
[Developer Guide](../4.Developer%20Guide/README.md).

**How do I control disk usage on a large repository?**
Run a pilot with `--max-files 200`, measure actual usage, then extrapolate.
For storage-sensitive environments add `--no-trigram` (the trigram field is
roughly seven times the source size and dominates storage), at the cost of
exact-substring search.

**Does re-indexing the same repository duplicate storage?**
No. Files are deduplicated by content hash; unchanged files only get an extra
revision label. The `reused` counter in the output shows how many were reused.

**Does indexing need an LLM API key?**
Not in the default sparse mode — sparse vectors are produced by Milvus's own
BM25 Function. A key is only needed when dense-vector mode is enabled.

## Retrieval

**Search returns `index_not_ready`.**
That collection or revision has no indexed data yet. Run `index` first and make
sure `--revision` matches the value used at index time (both default to `local`).

**What do the termination values mean?**

| Value | Meaning |
|---|---|
| `submitted` | The agent submitted its conclusion (normal path) |
| `stagnated` | No new findings for several consecutive search turns; stopped early |
| `max_turns` | Turn limit reached; collected results are returned |
| `no_tool_call` / `llm_error` | The model stopped calling tools or the call failed; collected results are returned |
| `index_not_ready` | Index unavailable; the retrieval loop never started |

**How do I trade off depth against token usage?**
`max_turns`, `stagnation_rounds`, `search_topk` and `retrieve_topk` in
`SearchAgentConfig` together determine search depth and token consumption.
Every result carries `total_input_tokens` and `total_output_tokens`; convert
them to money using the pricing of whichever endpoint you configured — the
result itself reports no monetary amount.

**Can I use local or third-party models?**
Yes. `LLMConfig` accepts any OpenAI-compatible endpoint, and the decision and
filter models are configured independently.

## Runtime

**Errors about SSL certificates or related configuration?**
The SDK uses the certifi CA bundle by default and configures the framework
accordingly. For self-signed certificates, set `LLMConfig.ssl_cert`.

**Workflow execution timeout?**
The framework's default workflow timeout is short; the SDK injects
`SearchAgentConfig.time_limit_seconds` (default 900s) per run. Increase it for
unusually deep searches.

**`docker ps` permission denied on Linux?**
Add your user to the docker group and log in again:
`sudo usermod -aG docker $USER`.
