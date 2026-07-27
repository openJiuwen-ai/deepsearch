# openjiuwen_deepsearch.framework.openjiuwen.agent.workflow — DeepSearchAgent

## class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent()
```
**DeepSearchAgent** 实现「search」模式的多步检索推理：初始化研究状态、从动作空间采样动作、在并发上限内执行工具与状态校验，并在找到答案或触发时间/次数等终止条件时结束。它继承 **`BaseAgent`**，当配置中 **`search_mode` 为 `"search"`** 时由 **`AgentFactory`** 创建（参见 [`agent_factory`](./agent_factory.md)）。

**实例字段**（在 `run` 过程中或构造后使用）：

- **version**（`str`）：子工作流卡片版本，默认 `"1"`。
- **action_pool**、**completed_actions**、**final_answer**：搜索循环运行时状态。
- **fail_count**、**total_input_tokens**、**total_output_tokens**：跨子工作流的计数。
- **log_dir**、**time_limit**、**query**、**gold_answer**、**tool_map**：单次运行的执行上下文。
- **agent_config**（`AgentConfig | None`）、**per_question_params**、**search_config**（`SearchWorkflowConfig | None`）：由入参 **`agent_config`** 及可选 **`service_config.search_workflow`** 校验得到。

---

### setup_log_directory
```python
setup_log_directory(save_as: str) -> None
```
在 **`{LogManager.get_log_dir()}/{save_as}`** 下创建 **`Action`** 与 **`Result`** 子目录，写入 **`log_dir`**，并设置 **`action_pool.log_dir`**。

**参数**：
- **save_as**（`str`）：日志根目录下的子目录名（`run` 中通常为 `result_{conversation_id}`）。

---

### run
```python
async run(
    message: str,
    conversation_id: str,
    agent_config: dict,
    report_template: str = "",
    interrupt_feedback: str = "",
) -> AsyncGenerator[str, None]
```
与 **`BaseAgent.run`** 签名一致。先经 **`validate_run_agent_params`**、再剥离可选字段后经 **`validate_agent_required_field`** 校验。将 **`agent_config`** 深拷贝为 **`AgentConfig`**，配置日志目录、从 **`agent_config["service_config"]["search_workflow"]`** 解析 **`SearchWorkflowConfig`**（解析失败则使用默认配置）、**`per_question_params`**、环境变量 **`WORKFLOW_EXECUTE_TIMEOUT`**、LLM 上下文（要求 **`llm_config`** 中存在 **`general`**），以及由 **`per_question_params.tool_map`** 决定的工具：

- **`"search_fetch"`**：注册 **`WebFetch`** 与 **`WebSearch`**。其中 **`WebFetch`** 通过 **`web_fetch_provider_config`** 显式解析 provider（v1 支持 **`provider_name="jina"`**），**`WebSearch`** 使用 **`web_search_engine_config`** 中配置的活动联网搜索引擎。
- **`"retrieve"`**：注册 **`RetrieveTool`**（Milvus / 向量化相关字段来自 **`search_workflow_milvus_config`**）。

### `MilvusConfig`（`search_workflow_milvus_config`）

当 `tool_map="retrieve"` 时，`MilvusConfig` 用于配置 DeepSearch 检索路径所依赖的 Milvus 向量库与 Embedding 服务。

**支持字段（含含义与默认值）：**
- **`milvus_host`**（`str`，默认 `"localhost"`）：Milvus 主机地址。
- **`milvus_port`**（`int`，默认 `19530`）：Milvus 服务端口。
- **`database_name`**（`str`，默认 `"deepsearch_benchmarks"`）：Milvus 数据库名。
- **`collection_name`**（`str`，默认 `"browsecompplus_with_bm25"`）：检索使用的集合名。
- **`embedder_model_name`**（`str`，默认 `""`）：Embedding 模型名称（须与索引构建时所用模型一致）。
- **`embedder_api_key`**（`bytearray`，默认空）：Embedding 服务 API Key。
- **`embedder_base_url`**（`str`，默认 `""`）：Embedding 服务地址（例如 `http://localhost:11450/v1/embeddings`）。
- **`embedder_timeout`**（`int`，默认 `100`）：Embedding 请求超时时间（秒）。
- **`retriever_class`**（可选，默认 `None`，`RetrieveTool` 使用 `KnowledgeBaseRetriever`）：检索实现类，例如对 `create_browsecompplus_index.py` 构建的索引使用 `BrowsecompPlusMilvusRetriever`。
- **`model_config`**（`dict`，可选）：要传递给嵌入器的额外模型配置字段。

**使用说明**：

- 若索引由 `create_browsecompplus_index.py`（openjiuwen_deepsearch/algorithm/search_index/create_browsecompplus_index.py）创建，可使用上述默认 Milvus 设置，并将 **`retriever_class`** 设为 **`BrowsecompPlusMilvusRetriever`**（见下方示例）。**retrieve 仍须在配置中填写与索引一致的 Embedding 服务地址、密钥及模型名。**

- 若索引由 openJiuwen studio「同步到 Deepsearch」构建，将 `collection_name` 设为 `"ds_kb_{kb_id}_chunks"`、`database_name` 设为 `"default"`；省略 **`retriever_class`** 即使用 **`KnowledgeBaseRetriever`**。

**基础用法示例（DeepSearch Agent API + Milvus retrieve 模式）：**
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

### `SearchWorkflowConfig`（`service_config.search_workflow`）

`SearchWorkflowConfig` 用于统一控制 DeepSearch 搜索子流程行为（初始状态、动作生成、状态扩展与校验策略等）。在 `run(...)` 中会从 `agent_config["service_config"]["search_workflow"]` 解析；若解析失败会自动回退到默认配置。

**支持字段（含含义与默认值）：**

- **`action_sampling`**（`ActionSamplingConfig`，默认 `ActionSamplingConfig()`）
  - `depth_weight`（`bool`，默认 `True`）：是否使用深度权重。
  - `promote_unique_states`（`bool`，默认 `False`）：是否提升唯一状态优先级。
  - `random_sample`（`bool`，默认 `False`）：是否随机采样动作。

- **`init_state_agent`**（`InitStateAgentConfig`，默认 `InitStateAgentConfig()`）
  - `max_tries`（`int`，默认 `10`）：初始化状态最大重试次数。
  - `llm_config`（`dict`，默认 `{}`）：该阶段 LLM 配置映射。

- **`find_action_agent`**（`FindActionAgentConfig`，默认 `FindActionAgentConfig()`）
  - `llm_config`（`dict`，默认 `{}`）：该阶段 LLM 配置映射。
  - `action_proposals_limit`（`int`，默认 `5`）：单轮动作提案上限。
  - `action_pool_depleted_strategy`（`"simple_retry" | "dependent_retry"`，默认 `"dependent_retry"`）：动作池耗尽时策略。

- **`state_creation_agent`**（`StateCreationAgentConfig`，默认 `StateCreationAgentConfig()`）
  - `log_fetch`（`bool`，默认 `False`）：是否记录抓取日志。
  - `log_search`（`bool`，默认 `False`）：是否记录搜索日志。
  - `web_fetch_log_file`（`str`，默认 `"gnosis/tool_log/web_fetch_log.jsonl"`）：抓取日志路径。
  - `web_search_log_file`（`str`，默认 `"gnosis/tool_log/web_search_log.jsonl"`）：搜索日志路径。
  - `use_candidate_strength`（`bool`，默认 `True`）：是否使用候选强度评分。
  - `discovered_clues_mode`（`"report" | "blacklist"`，默认 `"blacklist"`）：发现线索处理模式。
  - `max_llm_calls_per_run`（`int`，默认 `100`）：单次 state creation 最大 LLM 调用数。
  - `context_limit_reached_strategy`（`"fail" | "reduced_retrieval_request" | "delete_tool_responses" | "delete_tool_input_and_responses"`，默认 `"reduced_retrieval_request"`）：上下文超限时策略。
  - `llm_config`（`dict`，默认 `{}`）：该阶段 LLM 配置映射。
  - `retrieval_settings`（`RetrievalSettingsConfig`，默认 `RetrievalSettingsConfig()`）
    - `retrieval_prompt`（`"retrieve" | "retrieve_given_multihop_query"`，默认 `"retrieve"`）
    - `top_k`（`int`，默认 `3`）
    - `top_k_multiply_factor`（`int`，默认 `5`）
    - `add_instruction`（`bool`，默认 `True`）
    - `mode`（`"dense" | "sparse" | "hybrid"`，默认 `"hybrid"`）
  - `validator_agent`（`ValidatorAgentConfig`，默认 `ValidatorAgentConfig()`）
    - `validate_new_states`（`bool`，默认 `False`）
    - `validate_answer`（`bool`，默认 `False`）
    - `llm_config`（`dict`，默认 `{}`）

**`tool_map`** 取其他值会抛出 **`CustomValueException`**。

在写入 **`AgentConfig`** 前会从字典中 **`pop`** 的**可选**字段：

- **`service_config`**（`dict`）：其中的 **`search_workflow`** 会校验为 **`SearchWorkflowConfig`**。
- **`gold_answer`**（`str | None`）：可选标准答案（评测场景），会进入最终返回结构。
`gold_answer` 是一个可选的字符串字段，可以包含在传递给 `run(...)` 的 `agent_config` 中。代理在执行期间不会使用它，但会附加到最终的 `SearchFinalResult` 上以进行评估，从而允许将预测答案与参考答案进行比较。

**基本用法示例**：

**基础示例**：
```python
from openjiuwen_deepsearch.config.config import Config

agent_config = Config().agent_config.model_dump()
agent_config["gold_answer"] = "标准答案示例。"

async for chunk in agent.run(
    message="问题示例 ...",
    conversation_id="demo-conversation-id",
    report_template="",
    interrupt_feedback="",
    agent_config=agent_config,
):
    ...
```

**参数**：
- **message**（`str`）：用户问题（内部作为 **`query`**）。
- **conversation_id**（`str`）：用于日志子目录命名。
- **agent_config**（`dict`）：完整 Agent 配置，并可附带 **`service_config`** / **`gold_answer`**。
- **report_template**、**interrupt_feedback**：为与其它 Agent 统一的接口保留；本 Agent 主路径不使用。

**简单可运行示例（`search_fetch`）**：
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

    # search_fetch 配置（tool_map 默认是 "search_fetch"）。
    agent_config["web_fetch_provider_config"] = {
        "provider_name": "jina",
        "api_key": bytearray("<YOUR_JINA_API_KEY>", encoding="utf-8"),
        "base_url": "",
        "extension": {},
    }
    agent_config["web_search_engine_config"] = {
        "search_engine_name": "serper",  # 也可改为 tavily / jina / petal / bocha / perplexity / custom
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

**返回（生成器）**：
- 每次运行 **`yield`** 一条 JSON 字符串（`ensure_ascii=False`）：一般为 **`SearchFinalResult`** 的序列化结果。

**`SearchFinalResult` 字段含义**：
- **`question`**：本次运行处理的原始用户问题。
- **`termination`**：终止原因（例如 `answer`、`time_limit`、`actions_explored_limit`、`fail_limit`、`action_pool_depleted` 等）。
- **`completion_time`**：本次运行总耗时（秒）。
- **`current_date_time`**：生成最终结果时的 UTC 时间戳字符串（`YYYYMMDDHHMMSSmmm`）。
- **`prediction`**：最终预测答案文本（未找到答案时可为 `null`）。
- **`gold_answer`**：可选标准答案（常用于评测比对）。
- **`messages`**：search/react 执行路径最终消息历史快照。
- **`config`**：最终结果附带的运行元数据（例如 agent 标识）。
- **`retrieved_evidence_ids`**：工具执行过程中聚合得到的证据/文档 ID 列表。

**异常**：
- **`CustomValueException`**：运行参数非法、缺少 **`general`** LLM 配置、**`tool_map`** 非法，或初始化状态子工作流在重试后仍失败等。

---

### run_state_creation_workflow
```python
async run_state_creation_workflow(action: Any, semaphore: asyncio.Semaphore) -> Any
```
在给定信号量下为单个 **`Action`** 执行 **`state_creation`** 子图（供内部并行 worker 使用）。集成方请优先调用 **`run`**；仅在扩展 Agent 行为时再考虑直接调用。

---

## 相关文档

- **`AgentFactory.create_agent`**：配置 **`"search_mode": "search"`** 得到 **`DeepSearchAgent`**（[`agent_factory`](./agent_factory.md)）。
- **`BaseAgent`**、**`DeepresearchAgent`**：同模块概述见 [`workflow`](./workflow.md)。
- 会话/研究侧模型见 [`search_context`](./search_context.md)；**`SearchFinalResult`** 与之一同在上述 Python 模块中定义，用于 search 模式最终载荷。

---
