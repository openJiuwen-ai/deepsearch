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

- **`"search_fetch"`**: `WebFetch` + `WebSearch` (uses `jina_api_key`, `serper_api_key` on **`agent_config`**).
- **`"retrieve"`**: `RetrieveBrowsecompPlus` (Milvus / embedder fields from **`search_workflow_milvus_config`**).

Other values for **`tool_map`** raise **`CustomValueException`**.

**Optional keys** removed before **`AgentConfig`** validation:

- **`service_config`** (`dict`): nested **`search_workflow`** is validated as **`SearchWorkflowConfig`**.
- **`gold_answer`** (`str | None`): optional benchmark label; forwarded into the final payload.

**Parameters**:
- **message** (`str`): User question (**`query`** for the internal loop).
- **conversation_id** (`str`): Used to name the log subdirectory.
- **agent_config** (`dict`): Full agent configuration plus optional **`service_config`** / **`gold_answer`** as above.
- **report_template**, **interrupt_feedback**: Accepted for API compatibility; not used in this agent’s path.

**Simple runnable example (`search_fetch`)**:
```python
import asyncio
import os
from main import run_jiuwen_workflow
from openjiuwen_deepsearch.config.config import Config


async def main():
    query = "who was the president of the former country whose capital is known as the white city?"

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

    # search_fetch keys (tool_map defaults to "search_fetch").
    agent_config["jina_api_key"] = bytearray("<YOUR_JINA_API_KEY>", encoding="utf-8")
    agent_config["serper_api_key"] = bytearray("<YOUR_SERPER_API_KEY>", encoding="utf-8")

    # run_jiuwen_workflow is async.
    result = await run_jiuwen_workflow(query, agent_config, "")
    print("run_jiuwen_workflow returned:", result)  # usually None; outputs go to logs/output files


if __name__ == "__main__":
    asyncio.run(main())
```

**Yields**:
- One JSON string (UTF-8, `ensure_ascii=False`) per run: serialized **`SearchFinalResult`** (or dict-safe fallback). Fields match the Pydantic model in `openjiuwen_deepsearch.framework.openjiuwen.agent.search_context` (**`question`**, **`termination`**, **`completion_time`**, **`current_date_time`**, **`prediction`**, **`gold_answer`**, **`messages`**, **`config`**, **`retrieved_evidence_ids`**).

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
