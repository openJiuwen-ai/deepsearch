# Search Nodes

## 维护范围

本文档覆盖 DeepSearch 搜索智能体中的搜索节点能力，包括初始状态生成、候选 action 发现、action 执行、工具节点、LLM 调用封装、状态校验和结果保存。

本文档不覆盖 action pool 的采样细节和具体搜索工具实现。

## 功能目的

Search nodes 负责把 DeepSearch 搜索循环拆成可组合步骤：初始化状态、寻找可执行 action、执行 action、解析工具调用结果、校验新状态或最终答案。它们是搜索智能体的算法状态机。

## 可见行为

- 初始化节点输出 `State`，并设置 id 和 depth。
- find action 节点输出带 score 的 `ActionProposal` 列表。
- run action 节点按模式暴露 `web_search` / `web_fetch` 或 `retrieve` 工具。
- validate 节点决定新状态是否可继续探索或是否完成。
- LLM 输出解析失败时会进行有限重试或返回失败结构。

## 关键代码路径

- 初始状态：`openjiuwen_deepsearch/algorithm/search_nodes/initialize_state.py`
- action 发现：`openjiuwen_deepsearch/algorithm/search_nodes/find_action.py`
- action 执行：`openjiuwen_deepsearch/algorithm/search_nodes/run_action.py`
- 工具节点：`openjiuwen_deepsearch/algorithm/search_nodes/tool_node.py`
- LLM 调用：`openjiuwen_deepsearch/algorithm/search_nodes/llm_utils.py`
- 状态校验：`openjiuwen_deepsearch/algorithm/search_nodes/validate_new_state.py`
- 校验工具：`openjiuwen_deepsearch/algorithm/search_nodes/verify_utils.py`
- 通用工具：`openjiuwen_deepsearch/algorithm/search_nodes/utils.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_initialize_state.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_find_action_space.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_run_action.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_validator.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_verify.md`

主要测试：

- `tests/search_agent/test_initialize_state.py`
- `tests/search_agent/test_find_action_space.py`
- `tests/search_agent/test_run_action.py`
- `tests/search_agent/test_run_action_node.py`
- `tests/search_agent/test_save_result.py`
- `tests/search_agent/test_config_matrix.py`
- `tests/search_agent/test_model_switch_runtime_config.py`

## 核心流程

1. 初始化节点调用 Prompt 并解析 `State`。
2. find action 节点基于当前 state、result 和 query 生成候选 action。
3. action pool 选出待执行 action。
4. run action 节点构造工具定义，调用 LLM 并执行工具。
5. LLM 和工具消息被解析为新状态或答案。
6. validate 节点校验新状态和答案质量。
7. 结果被保存并交还搜索循环。

## 数据契约与依赖

核心数据：

- `State`
- `Action`
- `Result`
- LLM messages。
- tool calls。

运行依赖：

- LLM 配置。
- Prompt 模板系统。
- 搜索工具白名单。

## 边界与错误处理

- LLM thinking 标签会在解析前剥离。
- JSON 输出可经过 repair 或 normalize 后解析，但 schema 不满足时仍返回错误。
- find action 多次失败后返回空 actions，不应抛出裸异常。
- run action 只接受白名单中的工具名。
- 上下文长度错误按配置决定 fail 或 retry 策略。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/search_agent/test_initialize_state.py
uv run pytest tests/search_agent/test_find_action_space.py
uv run pytest tests/search_agent/test_run_action.py
uv run pytest tests/search_agent/test_run_action_node.py
```

## 相关文档

- [DeepSearch 搜索智能体总览](../search-agent.md)
- [Action Pool](./action-pool.md)
- [Search Tools](./search-tools.md)
- [Prompt 模板系统](../prompt-template-system.md)
