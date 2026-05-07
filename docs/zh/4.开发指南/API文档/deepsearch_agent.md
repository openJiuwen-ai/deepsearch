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

- **`"search_fetch"`**：注册 **`WebFetch`** 与 **`WebSearch`**（使用配置中的 **`jina_api_key`**、**`serper_api_key`**）。
- **`"retrieve"`**：注册 **`RetrieveBrowsecompPlus`**（Milvus / 向量化相关字段来自 **`search_workflow_milvus_config`**）。

**`tool_map`** 取其他值会抛出 **`CustomValueException`**。

在写入 **`AgentConfig`** 前会从字典中 **`pop`** 的**可选**字段：

- **`service_config`**（`dict`）：其中的 **`search_workflow`** 会校验为 **`SearchWorkflowConfig`**。
- **`gold_answer`**（`str | None`）：可选标准答案（评测场景），会进入最终返回结构。

**参数**：
- **message**（`str`）：用户问题（内部作为 **`query`**）。
- **conversation_id**（`str`）：用于日志子目录命名。
- **agent_config**（`dict`）：完整 Agent 配置，并可附带 **`service_config`** / **`gold_answer`**。
- **report_template**、**interrupt_feedback**：为与其它 Agent 统一的接口保留；本 Agent 主路径不使用。

**简单可运行示例（`search_fetch`）**：
```python
import asyncio
import os
from main import run_jiuwen_workflow
from openjiuwen_deepsearch.config.config import Config


async def main():
    query = "who was the president of the former country whose capital is known as the white city?"

    # Start from project defaults and only override what differs.
    agent_config = Config().agent_config.model_dump()
    agent_config["search_mode"] = "search"  # selects DeepSearchAgent, as default is "research"
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

    # 与示例保持一致：run_jiuwen_workflow 为 async 函数
    result = await run_jiuwen_workflow(query, agent_config, "")
    print("run_jiuwen_workflow returned:", result)  # 当前通常为 None（结果写日志/输出目录）


if __name__ == "__main__":
    asyncio.run(main())
```

**返回（生成器）**：
- 每次运行 **`yield`** 一条 JSON 字符串（`ensure_ascii=False`）：一般为 **`SearchFinalResult`** 的序列化结果。字段与 `openjiuwen_deepsearch.framework.openjiuwen.agent.search_context` 中的 Pydantic 模型一致（**`question`**、**`termination`**、**`completion_time`**、**`current_date_time`**、**`prediction`**、**`gold_answer`**、**`messages`**、**`config`**、**`retrieved_evidence_ids`** 等）。

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
