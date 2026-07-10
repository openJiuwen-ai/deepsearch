# `openjiuwen_deepsearch.framework.openjiuwen.agent.workflow` — `DeepSearchAgent`

## `DeepSearchAgent`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent()
```
**DeepSearchAgent** runs the multi-step “search” workflow: initialize research state, propose actions from an action space, execute tools in parallel (bounded by workers), validate new states, and stop when an answer is found or limits/timeouts apply. It subclasses **`BaseAgent`** and is constructed by **`AgentFactory`** when `search_mode` is `"search"` (see [`agent_factory`](./agent_factory.md)).

**Instance fields** (set during `run` or construction):

- **version** (`str`): Workflow card version, default `"1"`.
- **action_pool**, **completed_actions**, **final_answer**: runtime search loop state.
- **fail_count**, **total_input_tokens**, **total_output_tokens**: counters across sub-workflows.
- **log_dir**, **time_limit**, **query**, **gold_answer**, **tool_map**: per-run execution context.
- **agent_config** (`AgentConfig | None`), **per_question_params**, **search_config** (`SearchWorkflowConfig | None`): validated from the incoming `agent_config` and optional `service_config.search_workflow`.

---

### `setup_log_directory`
```python
setup_log_directory(save_as: str) -> None
```
Creates `{LogManager.get_log_dir()}/{save_as}/Action` and `.../Result`, sets **`log_dir`**, and assigns **`action_pool.log_dir`**.

**Parameters**:
- **save_as** (`str`): Subdirectory name under the base log directory (e.g. `result_{conversation_id}` from `run`).

---

### Output logs directory and files

Each `run(...)` call creates a per-conversation output directory:

- Base path: `LogManager.get_log_dir()` (commonly configured as `./output/logs` in entry scripts).
- Run folder: `result_{conversation_id}`.
- Full run path shape: `{base_log_dir}/result_{conversation_id}`.

Inside that folder, `DeepSearchAgent` writes:

- `Action/` — snapshots of action proposals from `find_action` steps.
- `Result/` — per-action execution outputs from `state_creation` steps.
- `action_pool.json` — live snapshot of pending/running/completed actions.
- `final_result.json` — final `SearchFinalResult` payload for the run.

Typical file names:

- `Action/action_{timestamp}_{uuid}.json`
- `Result/result_{timestamp}_{uuid}.json`
- `Result/answer_result_{timestamp}_{uuid}.json` (when an answer is found)
- `Result/error_result_{timestamp}_{uuid}.json` (when a step fails)

Example layout:

```text
output/logs/
  result_1234567890/
    Action/
      action_20260507081833921_9a82b4f4f33f46f08c6615e7c8e4ff2f.json
    Result/
      result_20260507081835542_5accc5b8f4cc498ab0b4f4f040afe76e.json
      answer_result_20260507081840117_2dd2bffc286b4fb3b6db95abf4f5fd8f.json
    action_pool.json
    final_result.json
```

Example `Action/action_*.json`:

```json
{
  "question": "who was the president of the former country whose capital is known as the white city?",
  "state": {
    "id": "0",
    "depth": 0,
    "answer_variable": 0,
    "retrieved_evidence_ids": [],
    "state": [
      {
        "id": 0,
        "type": "person",
        "question_clues": [],
        "discovered_clues": [],
        "candidate": null,
        "candidate_strength": null
      }
    ]
  },
  "proposals": [
    "Identify which country had a capital nicknamed 'the white city'",
    "Find presidents associated with that country"
  ],
  "scores": [0.83, 0.71],
  "action_ids": ["a1f1...", "b2e2..."],
  "message": []
}
```

Example `Result/result_*.json`:

```json
{
  "previous_state": {
    "id": "1",
    "depth": 1,
    "answer_variable": 0,
    "retrieved_evidence_ids": [],
    "state": []
  },
  "previous_action": "Identify which country had a capital nicknamed 'the white city'",
  "result": {
    "messages": [
      {"role": "assistant", "content": "The capital nickname points to Belgrade."}
    ],
    "new_states": [],
    "found_answer": null,
    "summary": null,
    "previous_action_id": "a1f1...",
    "retrieved_evidence_ids": []
  },
  "time_taken": 3.42
}
```

Example `final_result.json`:

```json
{
  "question": "who was the president of the former country whose capital is known as the white city?",
  "termination": "answer",
  "completion_time": 18.73,
  "current_date_time": "20260507081840119",
  "prediction": "Josip Broz Tito",
  "gold_answer": null,
  "messages": [
    {"role": "assistant", "content": "Final answer ..."}
  ],
  "config": {},
  "retrieved_evidence_ids": []
}
```

---

### `run`
```python
async run(
    message: str,
    conversation_id: str,
    agent_config: dict,
    report_template: str = "",
    interrupt_feedback: str = "",
) -> AsyncGenerator[str, None]
```
Same surface as **`BaseAgent.run`**. Validates with `validate_run_agent_params` and `validate_agent_required_field` (after stripping optional keys). Deep-copies **`agent_config`** into **`AgentConfig`**, sets up logging, **`SearchWorkflowConfig`** from `agent_config["service_config"]["search_workflow"]` (defaults on parse failure), **`per_question_params`**, **`WORKFLOW_EXECUTE_TIMEOUT`**, LLM context (requires `llm_config["general"]`), and tools from **`per_question_params.tool_map`**:

- **`"search_fetch"`**: `WebFetch` + `WebSearch`. `WebFetch` resolves its provider from `web_fetch_provider_config` (v1 supports explicit `provider_name="jina"`); `WebSearch` uses the active engine configured under `web_search_engine_config`.
- **`"retrieve"`**: `RetrieveTool` (Milvus / embedder fields from **`search_workflow_milvus_config`**).

### `MilvusConfig` (`search_workflow_milvus_config`)

`MilvusConfig` is used when `tool_map="retrieve"` to configure the Milvus-backed retrieval source and embedding endpoint used by DeepSearch search-mode actions.

**Supported fields (meaning and defaults):**
- **`milvus_host`** (`str`, default `"localhost"`): Milvus host address.
- **`milvus_port`** (`int`, default `19530`): Milvus service port.
- **`database_name`** (`str`, default `"deepsearch_benchmarks"`): Milvus database name.
- **`collection_name`** (`str`, default `"browsecompplus_with_bm25"`): collection to query.
- **`embedder_model_name`** (`str`, default `""`): embedding model id; must match the model used when the index was built.
- **`embedder_api_key`** (`bytearray`, default empty): embedding service API key.
- **`embedder_base_url`** (`str`, default `""`): embedding endpoint URL (for example `http://localhost:11450/v1/embeddings`).
- **`embedder_timeout`** (`int`, default `100`): embedding request timeout in seconds.
- **`retriever_class`** (optional, default `None` → `KnowledgeBaseRetriever` on `RetrieveTool`): retriever implementation class, e.g. `BrowsecompPlusMilvusRetriever` for indexes built with `create_browsecompplus_index.py`.
- **`model_config`** (`dict`, optional): extra model config fields to pass through to the embedder.

**Usage notes**:
- If the index is created by `create_browsecompplus_index.py` (openjiuwen_deepsearch/algorithm/search_index/create_browsecompplus_index.py), use the default Milvus settings and set **`retriever_class`** to **`BrowsecompPlusMilvusRetriever`** (see example below). **For `retrieve` you must still configure the embedding base URL, API key, and model name to match the indexer.**

- If the index is built using “Sync to Deepsearch” option from openJiuwen studio, set `collection_name` to `"ds_kb_{kb_id}_chunks"` and `database_name` to `"default"`; omit **`retriever_class`** to use **`KnowledgeBaseRetriever`**.


**Basic usage example (DeepSearch Agent API + Milvus retrieve mode):**
```python
import asyncio
import copy
import uuid
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.retriever import (
    BrowsecompPlusMilvusRetriever,
)
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory


async def main():
    agent_config = Config().agent_config.model_dump()
    agent_config["search_mode"] = "search"
    agent_config["workflow_human_in_the_loop"] = False
    agent_config["search_workflow_per_question_params"]["tool_map"] = "retrieve"

    agent_config["llm_config"]["general"] = {
        "model_name": "<YOUR_LLM_MODEL_NAME>",
        "model_type": "<YOUR_LLM_MODEL_TYPE>",
        "base_url": "<YOUR_LLM_BASE_URL>",
        "api_key": bytearray("<YOUR_LLM_API_KEY>", encoding="utf-8"),
        "hyper_parameters": {"temperature": 0.2, "top_p": 1.0},
        "extension": {},
    }

    agent_config["search_workflow_milvus_config"] = {
        "milvus_host": "127.0.0.1",
        "milvus_port": 19530,
        "database_name": "deepsearch_benchmarks",
        "collection_name": "browsecompplus_with_bm25",
        "embedder_model_name": "<YOUR_EMBEDDING_MODEL_NAME>",
        "embedder_api_key": bytearray("<YOUR_EMBEDDER_API_KEY>", encoding="utf-8"),
        "embedder_base_url": "http://localhost:11450/v1/embeddings",
        "embedder_timeout": 100,
        "retriever_class": BrowsecompPlusMilvusRetriever,
    }

    agent = AgentFactory().create_agent(copy.deepcopy(agent_config))
    async for _ in agent.run(
        message="Your question",
        conversation_id=str(uuid.uuid4()),
        report_template="",
        interrupt_feedback="",
        agent_config=agent_config,
    ):
        pass


if __name__ == "__main__":
    asyncio.run(main())
```

### `SearchWorkflowConfig` (`service_config.search_workflow`)

`SearchWorkflowConfig` controls DeepSearch search sub-workflow behavior (state initialization, action generation, state creation, and validation policies). In `run(...)`, it is parsed from `agent_config["service_config"]["search_workflow"]`; if parsing fails, defaults are used.

**Supported fields (meaning and defaults):**

- **`action_sampling`** (`ActionSamplingConfig`, default `ActionSamplingConfig()`)
  - `depth_weight` (`bool`, default `True`): whether to use depth-based weighting.
  - `promote_unique_states` (`bool`, default `False`): whether to promote unique states.
  - `random_sample` (`bool`, default `False`): whether to sample actions randomly.

- **`init_state_agent`** (`InitStateAgentConfig`, default `InitStateAgentConfig()`)
  - `max_tries` (`int`, default `10`): max retries for init-state generation.
  - `llm_config` (`dict`, default `{}`): phase-specific LLM config mapping.

- **`find_action_agent`** (`FindActionAgentConfig`, default `FindActionAgentConfig()`)
  - `llm_config` (`dict`, default `{}`): phase-specific LLM config mapping.
  - `action_proposals_limit` (`int`, default `5`): max action proposals per round.
  - `action_pool_depleted_strategy` (`"simple_retry" | "dependent_retry"`, default `"dependent_retry"`): strategy when action pool is depleted.

- **`state_creation_agent`** (`StateCreationAgentConfig`, default `StateCreationAgentConfig()`)
  - `log_fetch` (`bool`, default `False`): whether to log fetch operations.
  - `log_search` (`bool`, default `False`): whether to log search operations.
  - `web_fetch_log_file` (`str`, default `"gnosis/tool_log/web_fetch_log.jsonl"`): fetch log path.
  - `web_search_log_file` (`str`, default `"gnosis/tool_log/web_search_log.jsonl"`): search log path.
  - `use_candidate_strength` (`bool`, default `True`): whether to use candidate-strength scoring.
  - `discovered_clues_mode` (`"report" | "blacklist"`, default `"blacklist"`): discovered-clues handling mode.
  - `max_llm_calls_per_run` (`int`, default `100`): max LLM calls in one state-creation run.
  - `context_limit_reached_strategy` (`"fail" | "reduced_retrieval_request" | "delete_tool_responses" | "delete_tool_input_and_responses"`, default `"reduced_retrieval_request"`): strategy when context limit is hit.
  - `llm_config` (`dict`, default `{}`): phase-specific LLM config mapping.
  - `retrieval_settings` (`RetrievalSettingsConfig`, default `RetrievalSettingsConfig()`)
    - `retrieval_prompt` (`"retrieve" | "retrieve_given_multihop_query"`, default `"retrieve"`)
    - `top_k` (`int`, default `3`)
    - `top_k_multiply_factor` (`int`, default `5`)
    - `add_instruction` (`bool`, default `True`)
    - `mode` (`"dense" | "sparse" | "hybrid"`, default `"hybrid"`)
  - `validator_agent` (`ValidatorAgentConfig`, default `ValidatorAgentConfig()`)
    - `validate_new_states` (`bool`, default `False`)
    - `validate_answer` (`bool`, default `False`)
    - `llm_config` (`dict`, default `{}`)

Other values for **`tool_map`** raise **`CustomValueException`**.

**Optional keys** removed before **`AgentConfig`** validation:

- **`service_config`** (`dict`): nested **`search_workflow`** is validated as **`SearchWorkflowConfig`**.
- **`gold_answer`** (`str | None`): optional benchmark label; forwarded into the final payload.
`gold_answer` is an optional string field that can be included in the `agent_config` passed to `run(...)`. It is not used by the agent during execution but is attached to the final `SearchFinalResult` for evaluation purposes, allowing comparison between the predicted answer and a reference answer.

**Basic usage example**:
```python
from openjiuwen_deepsearch.config.config import Config

agent_config = Config().agent_config.model_dump()
agent_config["gold_answer"] = "The president was John Doe."

async for chunk in agent.run(
    message="Who was the president ...?",
    conversation_id="demo-conversation-id",
    report_template="",
    interrupt_feedback="",
    agent_config=agent_config,
):
    ...
```

**Parameters**:
- **message** (`str`): User question (**`query`** for the internal loop).
- **conversation_id** (`str`): Used to name the log subdirectory.
- **agent_config** (`dict`): Full agent configuration plus optional **`service_config`** / **`gold_answer`** as above.
- **report_template**, **interrupt_feedback**: Accepted for API compatibility; not used in this agent’s path.

**Simple runnable example (`search_fetch`) with explicit log output path**:
```python
import asyncio
import copy
import json
import uuid
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


async def main():
    query = "who was the president of the former country whose capital is known as the white city?"

    # Important: initialize LogManager before creating/running the agent.
    # Safety check in LogManager allows paths under ./output/logs.
    log_dir = "./output/logs/my_run_logs"
    LogManager.init(
        log_dir=log_dir,
        max_bytes=100 * 1024 * 1024,
        backup_count=20,
        level="INFO",
        is_sensitive=False,
    )

    # Start from project defaults and only override what differs.
    agent_config = Config().agent_config.model_dump()
    agent_config["search_mode"] = "search"  # default is "research"
    agent_config["workflow_human_in_the_loop"] = False  # default is True
    agent_config["search_workflow_per_question_params"]["time_limit"] = 300  # default is 4800
    agent_config["search_workflow_per_question_params"]["max_workers"] = 2  # default is 5

    # LLM for general reasoning in search mode.
    agent_config["llm_config"]["general"] = {
        "model_name": "<YOUR_LLM_MODEL_NAME>",
        "model_type": "<YOUR_LLM_MODEL_TYPE>",
        "base_url": "<YOUR_LLM_BASE_URL>",
        "api_key": bytearray("<YOUR_LLM_API_KEY>", encoding="utf-8"),
        "hyper_parameters": {"temperature": 0.2, "top_p": 1.0},
        "extension": {},
    }

    # search_fetch config (tool_map defaults to "search_fetch").
    agent_config["web_fetch_provider_config"] = {
        "provider_name": "jina",
        "api_key": bytearray("<YOUR_JINA_API_KEY>", encoding="utf-8"),
        "base_url": "",
        "extension": {},
    }
    agent_config["web_search_engine_config"] = {
        "search_engine_name": "serper",  # or tavily / jina / petal / bocha / perplexity / custom
        "search_api_key": bytearray("<YOUR_WEB_SEARCH_API_KEY>", encoding="utf-8"),
        "search_url": "",
        "max_web_search_results": 5,
        "extension": {},
    }

    conversation_id = str(uuid.uuid4())
    agent = AgentFactory().create_agent(copy.deepcopy(agent_config))
    async for chunk in agent.run(
        message=query,
        conversation_id=conversation_id,
        report_template="",
        interrupt_feedback="",
        agent_config=agent_config,
    ):
        payload = json.loads(chunk)
        print("SearchFinalResult:", json.dumps(payload, indent=2))

    print(f"Per-run artifacts written under: {log_dir}/result_{conversation_id}/")


if __name__ == "__main__":
    asyncio.run(main())
```

**Yields**:
- One JSON string (UTF-8, `ensure_ascii=False`) per run: serialized **`SearchFinalResult`** (or dict-safe fallback).

**`SearchFinalResult` field meanings**:
- **`question`**: Original user query handled by this run.
- **`termination`**: Stop reason (for example `answer`, `time_limit`, `actions_explored_limit`, `fail_limit`, `action_pool_depleted`, etc.).
- **`completion_time`**: Total runtime in seconds for this run.
- **`current_date_time`**: UTC timestamp string when final result is produced (`YYYYMMDDHHMMSSmmm`).
- **`prediction`**: Final predicted answer text (or `null` when none found).
- **`gold_answer`**: Optional benchmark/reference answer passed through for evaluation comparison.
- **`messages`**: Final message history snapshot used by the search/react execution path.
- **`config`**: Extra run metadata attached at finalization time (for example agent marker).
- **`retrieved_evidence_ids`**: Aggregated evidence/document IDs collected during tool execution.

**Raises**:
- **`CustomValueException`**: invalid run params, missing **`general`** LLM config, invalid **`tool_map`**, or init-state workflow failure after retries.

---

### `run_state_creation_workflow`
```python
async run_state_creation_workflow(action: Any, semaphore: asyncio.Semaphore) -> Any
```
Runs the **`state_creation`** subgraph for one **`Action`** under the given semaphore (used by the parallel worker loop). Prefer invoking **`run`** unless you extend the agent.

---

## Related

- **`AgentFactory.create_agent`**: use `"search_mode": "search"` to obtain **`DeepSearchAgent`** ([`agent_factory`](./agent_factory.md)).
- **`BaseAgent`**, **`DeepresearchAgent`**: same module; overview in [`workflow`](./workflow.md).
- Session / research models in [`search_context`](./search_context.md); **`SearchFinalResult`** lives in the same Python module for search-mode payloads.

---
