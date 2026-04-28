# FAQ

## 1. Installation issues

### 1. `uv python install` times out

**Error**

	Caused by: error decoding response body
	Caused by: request or response body error
	Caused by: operation timed out

![image1.png](https://raw.gitcode.com/user-images/assets/8895323/4153cb74-f65e-4dfe-b0f7-1118402f0348/image1.png 'image1.png')

**Fix**

① Mirror for standalone Python builds:

```sh
UV_PYTHON_INSTALL_MIRROR="https://registry.npmmirror.com/-/binary/python-build-standalone" uv python install
# or
uv python install --index https://registry.npmmirror.com/-/binary/python-build-standalone
```

Other mirrors:

```
https://python-standalone.org/mirror/astral-sh/python-build-standalone/
```

② Or raise HTTP timeout:

```sh
UV_HTTP_TIMEOUT=300 uv python install <version>
```

### 2. `uv` PyPI downloads time out

**Error**

	╰─▶ I/O operation failed during extraction
	╰─▶ Failed to download distribution due to network timeout. Try increasing UV_HTTP_TIMEOUT (current value: 30s).

![image.png](https://raw.gitcode.com/user-images/assets/8895323/29f0f4a7-6e5d-4876-bc3d-8f6fad4b2367/image.png 'image.png')

**Fix**

```sh
UV_INDEX_URL=http://mirrors.aliyun.com/pypi/simple/ uv add <package>
```

### 3. `uv` SSL / unknown issuer (unusual domains)

**Error**

	Caused by: client error (Connect)
	Caused by: invalid peer certificate: UnknownIssuer

![image3.png](https://raw.gitcode.com/user-images/assets/8895323/15933480-3a39-4b9a-9c1c-2ed743b2e029/image3.png 'image3.png')

**Fix** (temporary; only if you understand the risk):

```sh
uv sync --allow-insecure-host github.com --allow-insecure-host pypi.org --allow-insecure-host files.pythonhosted.org
```

(`--trusted-host` may be used similarly depending on `uv` version.)

## 2. Logs

### Log location

openJiuwen-DeepSearch logs usually live under **`output/logs/common`** at the repo root. Two streams:

- **common_warning.log** — warnings and above (quick error scanning).
- **common.log** — general service logging.

Notes:

- `common.log` is mostly DeepSearch; third-party libs typically log only `warning`/`error` to disk (not `debug`/`info`).
- Very long lines may be truncated except for a few high-value outputs (citations, full reports, etc.).

## 3. Model errors

### 3.1 Call failures / timeouts

Messages mentioning **stream error**, **timeout**, **OpenAI API**, or **Client connection error** usually mean the LLM call failed—network issues, bad endpoint, context overflow, or provider safety filters.

![Context overflow](../images/FAQ/超上下文.png)

If logs show `LLM wall-clock timeout after ...`, that is the outer business-layer timeout from `agent_llm_timeouts`, not the same thing as the underlying `service_config.llm_timeout`. In that case, check whether `agent_llm_timeouts` includes `default`, whether the matched rule is too small, or whether a rule was unintentionally set to `0`.

### 3.2 “Retry” / format non-compliance

**retry** in logs often indicates the model output failed validation; DeepSearch retries internally until it errors out (or stays at WARN).

### 3.3 Which nodes matter

Some retries are benign; others affect report quality:

**Higher impact** (may break structure or sections):

```
entry — whether reporting runs at all
outliner — full outline
planner — per-section plan (affects that section)
sub_reportor — subsection body
reportor — final assembly
```

**Lower impact** (local to one search iteration):

```
summary — single search summary
reflection — search depth for one loop
citation verify — provenance for one hit
```

### 3.4 Models must support tool / function calling

DeepSearch relies heavily on **function calling**. Models without that capability cannot drive the workflow end-to-end.

## 4. Web search / augmentation errors

### 4.1 Engine HTTP failures

`ERROR` lines such as **Search request failed** mean the configured engine is unreachable or misconfigured. Empty large “gathering” panels in the UI often mean the same.

### 4.2 Empty `search_results`

Search logs for **`TOOL END`**: check engine type and whether `search_results` is empty for the query.

- Always empty → engine/config outage.
- Empty only sometimes → transient engine issues.
- Sparse empty rows → likely no hits for that query (usually harmless).

## 5. Knowledge base / local search

### 5.1 Cannot create KB / connect to Milvus

Likely **`MILVUS_HOST` / `MILVUS_PORT`** wrong or Milvus not running.

Set them in `.env` to match your Milvus endpoint (default often `localhost:19530`).

### 5.2 `run` fails token validation

Error like `Input should be a valid string ... vector_store.token` when value is `None`.

If **`MILVUS_TOKEN`** is unset, `None` may be passed while the schema expects a string.

Set `MILVUS_TOKEN` in `.env` (empty string if Milvus has no auth—the service normalizes empty to a string).

### 5.3 Index build fails with SSL errors

Embedding over HTTPS may fail if `EMBEDDING_SSL_VERIFY` / `EMBEDDING_SSL_CERT` disagree with the server cert (self-signed, private CA, etc.).

Adjust `.env` (see `.env.example`):

- Skip server cert check: `EMBEDDING_SSL_VERIFY=false` or leave blank (this repo’s `server/main.py` treats blank as off).
- Public CA: `EMBEDDING_SSL_VERIFY=true`, `EMBEDDING_SSL_CERT` empty.
- Private CA: `EMBEDDING_SSL_VERIFY=true` and `EMBEDDING_SSL_CERT=<path-to-pem>`.

## 6. Service / API behavior

### 6.1 Deployment constraints

DeepSearch supports distributed deployment but **one process per machine** unless you adopt Redis checkpointer mode. Multiple instances on one host should use **`CHECKPOINTER_TYPE=redis`**.

With Redis checkpointer, **`DB_TYPE` must be `mysql`** and every instance must share the **same** MySQL (metadata lives in the app DB). Pairing `redis` with `sqlite` fails validation at startup.

Distributed mode also requires full object storage settings (`OBS_SERVER`, `OBS_BUCKET`, `OBS_REGION`, `OBS_ACCESS_KEY_ID`, `OBS_SECRET_ACCESS_KEY`); see installation docs. `in_memory` / `persistence` keep KB uploads on local disk—even if `OBS_*` exist, they are not used for KB files in those modes.

### 6.2 `conversation_id` rules

Except within a single task’s interrupt/resume flow, each SDK `run` should use a **new** `conversation_id`.

**Reuse** the same `conversation_id` only when:

- Resuming HITL clarification.
- Resuming outline interaction.
- Continuing post-report local editing after the report is done.

### 6.3 `space_id` vs local KB

For HTTP `run`, `space_id` defines the tenant/workspace boundary. Every KB id in `local_search_config.local_search_config_ids` must be registered under that `space_id` on the server; otherwise access is denied.

`DeepSearchAgentManager` caches agents using a hash of all fields that affect construction (excluding `message`, `conversation_id`, `interrupt_feedback`), including **`space_id`**, **`local_search_config`**, web search settings, `llm_config`, and switches—so changing KB or engine config under the same space also yields a fresh agent instance.

> `space_id` comes from the client; bind it to authenticated identity at the gateway.

## 7. Appendix

Shared error codes and node status definitions: [status_code.py](https://gitcode.com/openJiuwen/deepsearch/blob/dev/openjiuwen_deepsearch/common/status_code.py)

### macOS: `No module named 'greenlet'`

If `uv run start_backend.py` fails with missing **greenlet**, install it into the same environment, e.g. `uv pip install greenlet`, or add it to your dependency group and run `uv sync` again.
