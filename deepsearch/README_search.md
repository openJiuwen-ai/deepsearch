**Read this in:** English | [简体中文](./README_search_zh.md)

# Search Agent User Guide

## Table of contents

- [Overview](#overview)
- [Key features](#key-features)
- [Architecture](#architecture)
- [Workflow](#workflow)
- [Configuration reference](#configuration-reference)
- [Quick start](#quick-start)
- [BrowseComp-Plus Milvus indexing](#browsecomp-plus-milvus-indexing)

## Overview

The Search Agent (**DeepSearchAgent**) is an intelligent search agent built on the openJiuwen framework. It supports deep search and multi-step reasoning: it handles complex questions, calls tools over several steps, and returns accurate answers. It uses a **state-space search** model (maintain and expand states to reach an answer) and **async concurrency** so multiple reasoning branches can be explored in parallel, improving throughput.

## Key features

### Main theme: logical reasoning

1. **Entity awareness**: who / what / how things relate in the question  
2. **Tree-like reasoning**: multiple angles, similar to human deliberation  
3. **Parallel exploration**: try several solution paths at once  

### 1. Entity-awareness engine

- **Dynamic entity graph**: detect key entities and how they connect  
- **State trajectory**: per-entity history of state changes for traceable reasoning  

### 2. Tree-like reasoning network

- **Branch management**: decompose hard questions into a branching tree; branches can be explored concurrently  
- **Pruning**: deprioritize or drop low-value branches and focus capacity on promising ones  

### 3. Intelligent action exploration

- **Breadth vs depth**: avoid collapsing too early to a local optimum; keep diversity of approaches  
- **Score-guided sampling**: weighted random sampling favors high-confidence actions while preserving exploration  
- **Async concurrent execution**: `asyncio` schedules many `state_creation` tasks in parallel; **`ActionPool` is used from a single coroutine context and is not thread-safe**  

### 4. Other capabilities

- **Stateful reasoning**: multi-step reasoning driven by a state machine  
- **Tooling**: web search, retrieval, extraction-style tools, etc.  
- **Configurability**: tunable search and runtime parameters  

## Architecture

### Core building blocks

1. **Three workflows**
   - `init_state`: parse the question and build the initial search state  
   - `find_action`: propose feasible search **actions** from the current state  
   - `state_creation`: run an action, call tools, and expand the state space  

2. **State management**
   - `State`: one node in the search graph (variables, depth, id, evidence ids, etc.)  
   - `Action`: one executable search step (question, state, proposal metadata)  
   - `ActionPool`: pool of pending actions for sampling in the async loop (**not** thread-safe; sampling weights are influenced by `SearchWorkflowConfig.action_sampling`)  

3. **Tools**
   - `WebSearch`: query a search engine  
   - `WebFetch`: fetch and interpret page content  
   - `Retrieve`: dense/sparse/hybrid vector retrieval over a knowledge base  

### Workflow shape

In **`search`** mode the agent roughly follows:

1. **Initialize state** — entity-ish structure and initial variables  
2. **Discover actions** — candidate branches  
3. **Execute actions** — concurrent tool use  
4. **Answer** — emit a final answer when criteria are met  

```
┌─────────────┐
│  init_state │  → initial state (entities / variables)
└─────────────┘
      ↓
┌─────────────┐
│ find_action │  → candidate actions from current state
└─────────────┘
      ↓
┌─────────────┐
│state_creation│ → run actions, extend state (tools / validation)
└─────────────┘
      ↓
   (loop until answer or limits)
```

## Workflow

### 1. Initialization

```python
# 1. Initialize state
init_state = await Runner.run_workflow("init_state_1", {
    "query": query,
    "config": init_config,
    ...
})

# 2. Initial actions
actions = await Runner.run_workflow("find_action_1", {
    "state": init_state,
    "query": query,
    ...
})

# 3. Enqueue actions
action_pool.add(actions)
```

### 2. Search loop

```python
while not final_answer and time < time_limit:
    # 1. Sample from the pool
    sampled_actions = action_pool.sample(available_slots)

    # 2. Run state_creation concurrently
    for action in sampled_actions:
        task = asyncio.create_task(run_state_creation_workflow(action, tool_map, semaphore))
        running_tasks.add(task)

    # 3. Wait for completions
    done, _ = await asyncio.wait(running_tasks, ...)

    # 4. Handle results
    for task in done:
        result = await task
        if result.found_answer:
            return result

        # 5. Expand with new actions
        for new_state in result.new_states:
            new_actions = await Runner.run_workflow("find_action_1", {
                "state": new_state,
                "query": query,
                ...
            })
            action_pool.add(new_actions)
```

### 3. Action sampling

`ServiceConfig.search_workflow.action_sampling` (**`ActionSamplingConfig`**) works together with **`ActionPool`**:

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `depth_weight` | bool | True | Enable depth penalty / shallow bonus (same thresholds as in `action_pool.py`) |
| `promote_unique_states` | bool | False | Down-weight duplicate state hashes in the pool to encourage diversity |
| `random_sample` | bool | False | If True, draw actions uniformly at random and skip weighted sampling |

**`ActionPool`** scoring behavior includes:

- **Score blend**: `action.proposal.score` plus per-variable `candidate_strength`  
- **Depth shaping**: penalty when depth > 5, bonus when depth < 2  
- **Unique-state promotion**: when `promote_unique_states` is True, down-weight repeated state hashes  
- **Random mode**: when `random_sample` is True, use uniform random sampling  

## Configuration reference

All types live in **`openjiuwen_deepsearch/config/config.py`**. There are two top-level buckets:

- **`AgentConfig`**: user-facing knobs (LLM, keys, per-question search limits, Milvus for retrieve, etc.)  
- **`ServiceConfig`**: deployment/runtime defaults (workflow timeouts, **`search_workflow`**, telemetry-style flags, etc.)  

---

### Part 1 — `AgentConfig`

**Note:** Callers normally supply **`AgentConfig`**. The **search subgraph wiring** (init / find / state-creation agents) is configured under **`ServiceConfig.search_workflow`** — see Part 2.

Search-related pieces on **`AgentConfig`**:

- **`search_workflow_per_question_params`** — **`PerQuestionParams`** (`tool_map`, `time_limit`, …)  
- **`search_workflow_milvus_config`** — **`MilvusConfig`** when using **`retrieve`**  

#### 1. `PerQuestionParams` (`AgentConfig.search_workflow_per_question_params`)

Per-question (one search episode) limits.

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `max_workers` | int | 5 | Max concurrent coroutines running actions (tune for APIs / hardware) |
| `retry_count_on_empty_action_space` | int | 3 | When the pool is empty and no workers are busy, re-run **find_action** at most this many times |
| `time_limit` | int | 4800 | Wall-clock limit per question (seconds); default 80 minutes |
| `tool_map` | Literal["search_fetch", "retrieve"] | `"search_fetch"` | Which tool stack to use |
| `actions_explored_limit` | int | 200 | Each completed **`state_creation`** increments the counter; if `> 0` and the counter reaches this value, search stops. Increase for longer runs |
| `fail_limit` | int | 0 | Max consecutive failures; `0` means no cap |
| `answer_mode_top_k` | int | 1 | How many candidate answers to collect before picking the best; `<= 1` returns on first answer |
| `provide_best_guess` | bool | False | On timeout without a confirmed answer, whether to return the best guess by `candidate_strength` |

**`tool_map` modes**

1. **`search_fetch`** — uses **`WebSearch`** and **`WebFetch`**.  
   - Good for: live web, fresh pages, open-domain QA.  
   - Requires: **`web_search_engine_config`** for the selected search engine and **`web_fetch_provider_config`** for the selected fetch provider. The current fetch provider is `jina`; supported search engines include Tavily, Google, Jina, Serper, Bocha, Perplexity, Petal, Xunfei, and custom engines.

2. **`retrieve`** — uses **`Retrieve`** (vector store).  
   - Good for: KB QA, closed corpora, precise chunk retrieval.  
   - Requires: a built index (see [BrowseComp-Plus Milvus indexing](#browsecomp-plus-milvus-indexing)) and **`AgentConfig.search_workflow_milvus_config`** with **`milvus_host`**, **`milvus_port`**, **`database_name`**, **`collection_name`**, **`embedder_model_name`**, **`embedder_api_key`**, **`embedder_base_url`**, **`embedder_timeout`**. These align with index-script env vars such as **`MILVUS_URI`**, **`MILVUS_DB_NAME`**, **`EMBED_*`**; the indexer favors environment variables, while **retrieve** at runtime reads **`MilvusConfig`** on **`AgentConfig`**.

#### 2. `MilvusConfig` (`AgentConfig.search_workflow_milvus_config`, retrieve mode)

When **`tool_map == "retrieve"`**, point the agent at Milvus + the embedding HTTP API.

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `milvus_host` | str | `"localhost"` | Milvus host |
| `milvus_port` | int | `19530` | Milvus port |
| `database_name` | str | `"deepsearch_benchmarks"` | DB name |
| `collection_name` | str | `"browsecompplus_with_bm25"` | Collection name |
| `embedder_model_name` | str | `""` | Embedding model id; must match the model used when the index was built |
| `embedder_api_key` | bytearray | empty | Required for retrieve; empty breaks tool construction |
| `embedder_base_url` | str | `""` | Embeddings URL, e.g. `http://localhost:11450/v1/embeddings` |
| `embedder_timeout` | int | `100` | HTTP timeout (seconds) |

**Retriever `mode`**

- **`dense`**: vector similarity only  
- **`sparse`**: lexical / BM25-style  
- **`hybrid`**: fuse dense + sparse (recommended default in config)  

**Retrieval + merge behavior**

1. **Initial retrieval** — fetch up to **`top_k * top_k_multiply_factor`** chunks for the chosen **`mode`**.  
2. **Merge** — chunks from the same document (e.g. BrowseComp-Plus) merge into one “document block”; if a non-first chunk hits, the first chunk may be **backfilled** for context.  
3. **Final ranking** — merged blocks sort by their **max** chunk score; top **`top_k`** blocks go to the agent.

You may change defaults in **`config.py`** or merge overrides from **`Config().agent_config.model_dump()`** before **`AgentConfig.model_validate`**.

---

### Part 2 — `ServiceConfig`

**Note:** **`ServiceConfig`** is aimed at operators / SDK defaults: workflow timeouts, per-node retries, stats switches, and especially **`search_workflow`**.

The **search workflow** is **`ServiceConfig.search_workflow`**: type **`SearchWorkflowConfig`**, containing init / find / state-creation agent configs.

#### `SearchWorkflowConfig` (`ServiceConfig.search_workflow`)

`SearchWorkflowConfig` bundles **action sampling** plus **three agent configs**:

| Field | Type | Description |
|------|------|-------------|
| `action_sampling` | ActionSamplingConfig | Depth weighting, unique-state down-weighting, random sampling (see [Action sampling](#3-action-sampling)) |
| `init_state_agent` | InitStateAgentConfig | Initial state LLM subgraph |
| `find_action_agent` | FindActionAgentConfig | Action proposal subgraph |
| `state_creation_agent` | StateCreationAgentConfig | Tool execution + expansion; includes **`retrieval_settings`**, **`validator_agent`**, … |

For each sub-agent, **`llm_config`** is a **`Dict[Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig]`** — the same idea as top-level **`AgentConfig.llm_config`**, but **without** **`vlm_chart_generating`**.

**`InitStateAgentConfig`** (`search_workflow.init_state_agent`)

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `max_tries` | int | 10 | Max retries for init |
| `llm_config` | see above | `{}` | Per-category LLM configs |

**`FindActionAgentConfig`** (`search_workflow.find_action_agent`)

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `llm_config` | see above | `{}` | Per-category LLM configs |
| `action_proposals_limit` | int | 5 | Max proposed actions per **find_action** call |
| `action_pool_depleted_strategy` | Literal["simple_retry", "dependent_retry"] | `"dependent_retry"` | When the pool is empty: **`simple_retry`** re-runs find_action with minimal context; **`dependent_retry`** adds context about explored directions |

**`ValidatorAgentConfig`** (`state_creation_agent.validator_agent`)

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `validate_new_states` | bool | False | Validate newly created states |
| `validate_answer` | bool | False | Validate final answers |
| `llm_config` | see above | `{}` | Per-category LLM configs |

**`RetrievalSettingsConfig`** (`state_creation_agent.retrieval_settings`)

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `retrieval_prompt` | Literal["retrieve", "retrieve_given_multihop_query"] | `"retrieve"` | Prompt template for LLM-generated retrieval queries. **`retrieve`**: simple keyword-style queries (BrowseComp+ default). **`retrieve_given_multihop_query`**: multi-hop style (GEAR-like; less validated). |
| `top_k` | int | 3 | Final number of document blocks returned to the agent |
| `top_k_multiply_factor` | int | 5 | Initial pool size multiplier: **`top_k × top_k_multiply_factor`** candidates before merge/rerank |
| `add_instruction` | bool | True | Append extra instructions when the LLM drafts retrieval queries; often helps with instruction-aware embedding backends |
| `mode` | Literal["dense", "sparse", "hybrid"] | `"hybrid"` | **`dense`** / **`sparse`** / **`hybrid`** retrieval |

**`StateCreationAgentConfig`** (`search_workflow.state_creation_agent`)

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `log_fetch` | bool | False | Log retrieval / fetch traffic |
| `log_search` | bool | False | Log search traffic |
| `web_fetch_log_file` | str | `"gnosis/tool_log/web_fetch_log.jsonl"` | Fetch / retrieval log path |
| `web_search_log_file` | str | `"gnosis/tool_log/web_search_log.jsonl"` | Web search log path |
| `use_candidate_strength` | bool | True | Feed **candidate_strength** into action scoring |
| `discovered_clues_mode` | Literal["report", "blacklist"] | `"blacklist"` | **`report`**: surface clues; **`blacklist`**: suppress repeats |
| `max_llm_calls_per_run` | int | 100 | Cap LLM calls inside one **state_creation** run |
| `context_limit_reached_strategy` | Literal["fail", "reduced_retrieval_request", "delete_tool_responses", "delete_tool_input_and_responses"] | `"reduced_retrieval_request"` | When context overflows during run-action: **`reduced_retrieval_request`** halves **`top_k`** / **`top_k_multiply_factor`** and retries (retrieve path only) |
| `llm_config` | see above | `{}` | Per-category LLM configs |
| `retrieval_settings` | RetrievalSettingsConfig | factory defaults | Retrieval behavior only (**no** Milvus host — that stays on **`AgentConfig.search_workflow_milvus_config`**) |
| `validator_agent` | ValidatorAgentConfig | factory defaults | Validator subgraph |

Common fields on **`LLMConfig`** include **`model_name`**, **`model_type`**, **`base_url`**, **`api_key`**, **`hyper_parameters`**, **`timeout`**, **`max_tries`**, **`append_think_tags_to_messages`** — see **`LLMConfig`** in `openjiuwen_deepsearch/config/config.py`.

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `model_name` | str | `""` | Model id (required) |
| `model_type` | Literal["openai", "siliconflow"] | `"openai"` | Backend flavor (matches **`main.py`** CLI) |
| `base_url` | str | `""` | HTTP base URL |
| `api_key` | bytearray | empty | API key (required) |
| `hyper_parameters` | dict | `{}` | e.g. temperature, max_tokens |
| `extension` | dict | `{}` | Vendor-specific extensions |
| `timeout` | int | 600 | HTTP timeout (seconds) |
| `max_tries` | int | 4 | Max retries per call |
| `append_think_tags_to_messages` | bool | False | Append “think” tags to messages |

#### Other `ServiceConfig` fields

See the full **`ServiceConfig`** model in **`openjiuwen_deepsearch/config/config.py`** for workflow timeouts, collector limits, tracer concurrency, and debug flags.

---

## Quick start

### Install

```bash
# From repository root
pip install .
```

### Tests and coverage (search mode)

```bash
# Default: skip real LLM / network tests (CI-friendly)
pytest -q tests/search_agent -m "not llm"

# All search_agent tests (llm tests need keys + explicit flag)
export RUN_LLM_TESTS=1
pytest -q tests/search_agent

# Mock orchestration / integration only
pytest -q tests/search_agent -m integration

# Real LLM + search_fetch (needs OPENROUTER_API_KEY, JINA_API_KEY, SERPER_API_KEY)
RUN_LLM_TESTS=1 pytest -q tests/search_agent -m llm

# Qwen-only branch (benchmarking/qwen_config.json + small_qwen); fixed log dir
export LLM_E2E_LOG_DIR="$PWD/tmp_llm_e2e_logs"
export RUN_LLM_TESTS=1
pytest -q tests/search_agent/test_llm_search_fetch_e2e.py -m llm -k "small_qwen"

# Coverage (CI-friendly: excludes llm)
pytest -q tests/search_agent -m "not llm" \
  --cov=openjiuwen_deepsearch \
  --cov-report=term-missing \
  --cov-fail-under=40
```

Notes:

- **`pytest-cov`** is in the dev dependency group.  
- Start **`--cov-fail-under`** low (e.g. `40`) and raise it as tests grow.  
- Markers: **`unit`**, **`integration`**, **`llm`**. **`llm`** tests require **`RUN_LLM_TESTS=1`** and keys; otherwise they **skip**.  
- Optional **`LLM_E2E_LOG_DIR`**: when set, **`test_llm_search_fetch_e2e`** writes under **`<dir>/<profile>/`** (e.g. **`gpt_mini_stack`**, **`small_qwen`**); otherwise pytest’s **`tmp_path`**.  
- **`-k small_qwen`** filters the Qwen profile (`qwen_config.json` / **`small_qwen`**).

### Environment variables

Typical keys (your app usually reads **`AgentConfig`** / CLI; env vars help locally and in CI):

```bash
# Generic OpenAI-compatible gateway
export OPENAI_API_KEY="your_openai_api_key"

# search_fetch: map the selected generic search and fetch provider credentials into agent_config
export JINA_API_KEY="your_jina_api_key"
export SERPER_API_KEY="your_serper_api_key"

# tests/search_agent @pytest.mark.llm e2e (e.g. test_llm_search_fetch_e2e) expect:
# RUN_LLM_TESTS=1 and OPENROUTER_API_KEY, JINA_API_KEY, SERPER_API_KEY

# retrieve: embedding secret belongs in AgentConfig.search_workflow_milvus_config.embedder_api_key;
# if you only have EMBEDDER_API_KEY in the shell, map it when building agent_config
export EMBEDDER_API_KEY="your_embedder_api_key"
```

### Programmatic usage

```python
import asyncio
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.config.config import Config, AgentConfig

async def main():
    agent_factory = AgentFactory()

    agent_config = Config().agent_config.model_dump()
    agent_config["search_mode"] = "search"
    # ... merge overrides (keys, milvus, service_config["search_workflow"], etc.)

    candidate_config = AgentConfig.model_validate(agent_config)
    agent_config = candidate_config.model_dump()
    agent = agent_factory.create_agent(agent_config)

    query = "..."
    async for chunk in agent.run(
        message=query,
        conversation_id="test_session_001",
        agent_config=agent_config,
    ):
        print(chunk)

asyncio.run(main())
```

### CLI example

Two ways to tune defaults:

1. Edit defaults in **`openjiuwen_deepsearch/config/config.py`**.  
2. Use **`main.py`**: it starts from **`Config().agent_config.model_dump()`** and overlays CLI flags.

**Retrieve mode:** build an index first (below), then fill **`AgentConfig.search_workflow_milvus_config` (`MilvusConfig`)**. Do **not** put Milvus connection strings in **`RetrievalSettingsConfig`** (that type only holds retrieval behavior like **`top_k`** and **`mode`**).

**Search mode CLI:** **`main.py`** requires full LLM flags. For **`search_fetch`**, configure the selected search engine with `--web_search_engine_name` and `--web_search_api_key`, and the selected fetch provider with `--fetch_provider_name` and `--fetch_api_key`. Pass the question with **`--query`** (multiple tokens are joined with spaces).

```bash
python -m main \
  --mode query \
  --search_mode search \
  --tool_map search_fetch \
  --query "Your question here" \
  --llm_model_name "your-model" \
  --llm_model_type openai \
  --llm_base_url "https://api.example.com/v1" \
  --llm_api_key "your-llm-key" \
  --web_search_engine_name serper \
  --web_search_api_key "your-serper-key" \
  --fetch_provider_name jina \
  --fetch_api_key "your-jina-key"
```

This repository **does not** ship **`scripts_will_be_deleted_later.run_8_queries`** or similar batch benchmark drivers by default; add your own if needed.

## BrowseComp-Plus Milvus indexing

This repo includes tooling to prepare the **BrowseComp-Plus** benchmark corpus and index it into **Milvus** for deep-research-style evaluation: download / decrypt from Hugging Face, chunk long pages, and build a **hybrid (dense + BM25)** searchable collection.

---

### 1. Overview

Steps at a glance:

- **Decrypt** — fetch and de-obfuscate the Hugging Face dataset  
- **Process** — chunk long HTML/text with **`TokenizerChunker`**  
- **Index** — upsert dense vectors + BM25 sparse features into Milvus  

#### Quick path

1. Run Milvus per the official guide: [Milvus standalone (Docker)](https://milvus.io/docs/install_standalone-docker.md).  
2. Run an **OpenAI-compatible embeddings** HTTP service compatible with **`OpenJiuwenAPIEmbedder`** (see `openjiuwen_deepsearch/algorithm/search_tools/retrieval/embedder.py`).  
3. Configure **`openjiuwen_deepsearch/algorithm/search_index/create_browsecompplus_index.py`** via **environment variables or module-level constants** (the script prefers **`_env(...)`**). **Before indexing, set** **`EMBED_API_URL`**, **`EMBED_API_KEY`**, **`EMBED_MODEL_NAME`**, and **`HUGGINGFACE_MODEL_NAME`** (no in-code defaults for the model / tokenizer ids).  
4. Run:

```bash
uv add datasets transformers
# Run any separate download / decrypt scripts first if your fork provides them
uv run -m openjiuwen_deepsearch.algorithm.search_index.create_browsecompplus_index
```

The JSONL should already be decrypted (e.g. **`browsecompplus.jsonl`**) and match **`DATA_LOCATION`**.

---

#### Dataset record shape

Each example typically includes:

- **`query_id`** — stable id  
- **`query`** — hard reasoning question  
- **`answer`** — gold string  
- **`evidence_docs`** — supporting docs (`docid`, `text`, `url`, …)  
- **`gold_docs`** — docs that contain the answer  
- **`negative_docs`** — hard negatives for retrieval evaluation  

---

### 2. Dataset notes

**BrowseComp-Plus** isolates **retriever** vs **LLM agent** effects for deep-research stacks, using a fixed ~**100k** web-document slice. The public drop is obfuscated to reduce leakage.

---

### 3. Install (indexer)

```bash
uv add datasets transformers pymilvus requests tqdm
```

You also need:

- A running Milvus ([install doc](https://milvus.io/docs/install_standalone-docker.md))  
- A reachable **OpenAI-compatible** embedding server (model id must match **`EMBED_MODEL_NAME`**)  

---

### 4. Index into Milvus

After preparing decrypted JSONL, set **`DATA_LOCATION`**, **`EMBED_API_URL`**, **`EMBED_API_KEY`**, and **`MILVUS_*`** as in the table below, then:

```bash
uv run -m openjiuwen_deepsearch.algorithm.search_index.create_browsecompplus_index
```

The script:

1. Connects to Milvus (default **`MILVUS_URI=http://localhost:19530`**, plus **`MILVUS_TOKEN`**, etc.) and selects / creates the database  
2. Builds **`OpenJiuwenAPIEmbedder`** (`api_url`, `api_token`, `timeout`)  
3. Loads **`DATA_LOCATION`** JSONL  
4. Chunks with **`BrowseCompChunker`** (**`TokenizerChunker`**, up to **2048** tokens per chunk)  
5. Embeds in batches and writes dense + BM25 sparse rows into **`MILVUS_COLLECTION_NAME`**

---

### 5. Configuration table

Variables in **`create_browsecompplus_index.py`** are read with **`_env("KEY", default)`**; if the env var is unset, the in-file default applies (**`EMBED_MODEL_NAME`** and **`HUGGINGFACE_MODEL_NAME`** default to empty and must be set).

| Variable (env name) | Meaning | Typical in-code default |
|------|------|----------------|
| `DATA_LOCATION` | Path to decrypted JSONL | `browsecompplus.jsonl` |
| `MILVUS_URI` | Milvus HTTP URI | `http://localhost:19530` |
| `MILVUS_TOKEN` | Auth token | `root:Milvus` |
| `MILVUS_DB_NAME` | Database name | `deepsearch_benchmarks` |
| `MILVUS_COLLECTION_NAME` | Collection name | `browsecompplus_with_bm25` |
| `HUGGINGFACE_MODEL_NAME` | HF tokenizer id for chunking | `Qwen/Qwen3-Embedding-8B` |
| `EMBED_MODEL_NAME` | Embedding model id passed to the API | **empty** (must set) |
| `EMBED_API_URL` | Embeddings HTTP URL | **empty** (must set or startup fails) |
| `EMBED_API_KEY` | Embeddings API key | **empty** (must set or startup fails) |
| `EMBED_TIMEOUT` | HTTP timeout (seconds) | `60` |
| `BATCH_SIZE` | Docs per batch | `10` |
| `INDEX_MAX_RECORDS` | Index only first N rows; `0` = all | `0` |

---

### 6. Main-block flow (`if __name__ == "__main__"`)

Aligned with the current script:

1. **Milvus client** — **`MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN, database=...)`**  
2. **Database** — create **`MILVUS_DB_NAME`** if missing; **`using_database`**  
3. **Embedder** — require **`EMBED_API_URL`**, **`EMBED_API_KEY`**, **`EMBED_MODEL_NAME`**, and **`HUGGINGFACE_MODEL_NAME`**; **`OpenJiuwenAPIEmbedder(...)`**; timeout falls back to **60s** if **`EMBED_TIMEOUT ≤ 0`**; tokenizer from **`HUGGINGFACE_MODEL_NAME`** for **`BrowseCompChunker`**  
4. **Load JSONL** — **`read_jsonl`**, build **`doc_id2doc`**, query-id maps, …  
5. **Schema** — **`setup_milvus_collection()`**: PK **`id`**, dense **`embedding`**, **`content`**, sparse **`content_sparse`**, BM25 function, **`AUTOINDEX` + COSINE** for dense, **`SPARSE_INVERTED_INDEX`** for sparse  
6. **Index** — **`BrowseCompChunker`** + **`index_documents_milvus()`** calling **`encoder_model.encode()`**  

---

### 7. Instruction tuning

**`OpenJiuwenAPIEmbedder`** can prepend a fixed task instruction on the query path when **`encode(..., is_query=True)`** is used. Many **instruction-tuned** embedding models benefit from this pattern.

- **Query time** — prepend a task instruction, e.g. *“Given a web search query, retrieve relevant passages that answer the query”*.  
- **Indexing time** — **do not** prepend instructions; encode raw document text for stable vectors.

The embedder’s **`encode(..., is_query=True|False)`** flag controls this: **`True`** adds the task instruction; **`False`** is for indexing.

---

### 8. Milvus schema (summary)

Matches **`setup_milvus_collection()`** in **`create_browsecompplus_index.py`**:

- **`id`** — string PK (`{docid}__{chunk_idx}`)  
- **`embedding`** — dense vector; dimension must match the embedding model used at index time; **COSINE**  
- **`content`** — raw chunk text (BM25 analyzer input)  
- **`content_sparse`** — Milvus BM25 sparse vector  
- **`docid`** — source document id  
- **`title`**, **`authors`**, **`datetime`** — parsed metadata  
- **`gold_query_id`**, **`evidence_query_id`** — related query id lists  

---

*This guide is the English counterpart to [`README_search_zh.md`](./README_search_zh.md). Both versions should track `openjiuwen_deepsearch/config/config.py` and `main.py`.*
