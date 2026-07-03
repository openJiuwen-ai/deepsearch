# DeepSearch 搜索子工作流

## 维护范围

本文档覆盖 `DeepSearchAgent` 和 `SimpleReactSearchAgent` 的 framework 编排行为，包括 DeepSearch 三个子 workflow、
运行时配置注入、并发 action 执行、终止条件和流式返回边界。

不覆盖搜索算法节点内部 Prompt 和工具调用解析细节；这些内容见 [DeepSearch 搜索智能体](../algorithm/search-agent.md)。

## 功能目的

DeepSearch 搜索子工作流用于在 `search_mode=search` 或 `search_mode=react` 下回答搜索问题。Framework 层负责把算法节点包装为
openJiuwen workflow，并管理 action pool、并发、日志目录、工具 map 和最终结果输出。

## 可见行为

- `search` 模式构造 `init_state`、`find_action`、`state_creation` 三个子 workflow。
- `state_creation` 可在 `run_action`、`tool`、`validate_new_state` 之间循环，直到生成新状态或结束。
- 多个 action 按 `max_workers` 并发执行。
- 遇到答案、时间上限、探索次数上限、失败次数上限或 action pool 耗尽时停止。
- action pool 耗尽后可按配置再次运行 `find_action`。
- `react` 模式走单轮 ReAct 式搜索工具调用，返回同一种 `SearchFinalResult`。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`
- `openjiuwen_deepsearch/algorithm/search_agent/action_pool.py`
- `openjiuwen_deepsearch/algorithm/search_nodes/`
- `tests/search_agent/test_deep_search_agent_smoke.py`
- `tests/search_agent/test_integration_search_loop.py`
- `tests/search_agent/test_simple_react_toolmap_logging.py`

## 核心流程

1. `DeepSearchAgent._build_agent` 注册 `init_state`、`find_action`、`state_creation` 子 workflow。
2. `_run_internal` 先执行 `init_state_1`，得到初始 `State`。
3. 执行 `find_action_1` 生成初始 action 列表并写入 `ActionPool`。
4. 循环期间按可用 worker 从 `ActionPool` 采样 action。
5. 每个 action 通过 `state_creation_1` 并发探索，必要时调用工具和状态校验节点。
6. 子任务完成后把新状态、答案候选和失败计数合并回主搜索循环。
7. 达到终止条件后保存并返回 `SearchFinalResult`。

## 数据契约与依赖

- DeepSearch 状态模型来自 `State`、`Action`、`Result`、`SearchFinalResult`。
- 子 workflow 运行时通过 `_subworkflow_context_inputs` 传入当前 `agent_config` 和 `search_config`，避免全局注册 workflow 复用旧配置。
- `per_question_params` 控制 worker 数、动作探索上限、失败上限、是否给 best guess、action pool 空时重试次数等。
- `tool_map` 决定可用工具；包含 `retrieve` 时，`state_creation` 会进入 retrieval-only 标记。

## 边界与错误处理

- `init_state` 失败会抛出 `AGENT_INIT_STATE_ERROR`。
- 运行中取消剩余 task 时，会把对应 action 记录为已完成但结果为空。
- action pool 空且重试耗尽时，以 `ACTION_POOL_DEPLETED` 终止。
- 达到时间、动作数或失败数限制时分别以对应 termination 原因返回。
- 敏感日志模式下不会打印 action、state 等完整内容。

## 测试与验证

- `uv run pytest tests/search_agent/test_deep_search_agent_smoke.py`
- `uv run pytest tests/search_agent/test_integration_search_loop.py`
- `uv run pytest tests/search_agent/test_model_switch_runtime_config.py`
- `uv run pytest tests/search_agent/test_run_action_node.py`
- `uv run pytest tests/search_agent/test_simple_react_toolmap_logging.py`

## 相关文档

- [Agent 工厂与运行模式](./agent-factory.md)
- [搜索上下文与数据契约](./search-context.md)
- [搜索工具注册与运行时 API 工具](./search-tool-registration.md)
- [DeepSearch 搜索智能体](../algorithm/search-agent.md)
