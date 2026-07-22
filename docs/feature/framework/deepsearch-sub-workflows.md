# DeepSearch 搜索子工作流

## 维护范围

本文档覆盖 `DeepSearchAgent` 和 `SimpleReactSearchAgent` 的 framework 编排行为，包括 DeepSearch 三个子 workflow、
运行时配置注入、单问题内并发 action 执行、多运行并行隔离、终止条件和流式返回边界。

不覆盖搜索算法节点内部 Prompt 和工具调用解析细节；这些内容见 [DeepSearch 搜索智能体](../algorithm/search-agent.md)。

## 功能目的

DeepSearch 搜索子工作流用于在 `search_mode=search` 或 `search_mode=react` 下回答搜索问题。Framework 层负责把算法节点包装为
openJiuwen workflow，并管理 action pool、并发、日志目录、工具 map 和最终结果输出。

`DeepSearchAgent` 把一次问题运行的可变状态放入 `DeepSearchRunContext`，并复用进程内共享的子 workflow agent，使同一 Agent 实例或缓存实例上的重叠运行互不串扰。

## 可见行为

- `search` 模式构造 `init_state`、`find_action`、`state_creation` 三个子 workflow。
- `state_creation` 可在 `run_action`、`tool`、`validate_new_state` 之间循环，直到生成新状态或结束。
- 单个问题内，多个 action 按 `max_workers` 并发执行。
- 多个问题可并行运行：每次 `run` 使用独立的 `DeepSearchRunContext`（query、action pool、log_dir、token 计数、fail_count、final_answer、配置副本等）。
- 子 workflow agent 在进程内按类级别共享；不同 `DeepSearchAgent` 实例复用同一套 `init_state` / `find_action` / `state_creation` 注册。
- 每次运行通过 `_subworkflow_context_inputs` 注入当前 `agent_config` 和 `search_config`，重叠运行的模型配置彼此隔离。
- `per_question_params.time_limit` 写入 `workflow_session_vars` 中的 workflow execute timeout，不再修改进程级 `os.environ`，因此不同运行的超时互不影响。
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
- `tests/search_agent/test_model_switch_runtime_config.py`
- `tests/search_agent/test_termination.py`
- `tests/search_agent/test_simple_react_toolmap_logging.py`
- `tests/server/test_agent_manager.py`

## 核心流程

1. `DeepSearchAgent.run` 校验并深拷贝本次 `agent_config`，解析 `search_config` 与 `per_question_params`。
2. 为本次运行创建独立 `ActionPool` 和日志目录，组装 `DeepSearchRunContext`。
3. 通过 contextvar 设置 LLM、工具、web search 和 `workflow_session_vars` 超时；`_build_agent` 取得或创建类级共享子 workflow agent。
4. `_run_internal(run_context)` 先执行 `init_state_1`，得到初始 `State`。
5. 执行 `find_action_1` 生成初始 action 列表并写入该 run 的 `ActionPool`。
6. 循环期间按可用 worker 从该 run 的 `ActionPool` 采样 action。
7. 每个 action 通过 `state_creation_1` 并发探索，必要时调用工具和状态校验节点；结果写回同一 `run_context`。
8. 达到终止条件后保存并返回 `SearchFinalResult`；`finally` 中 flush snapshot、reset contextvar，并清零本次配置中的密钥字段。

## 数据契约与依赖

- DeepSearch 状态模型来自 `State`、`Action`、`Result`、`SearchFinalResult`。
- `DeepSearchRunContext` 持有单次运行的 `agent_config`、`search_config`、`per_question_params`、`query`、`log_dir`、`time_limit`、`tool_map`、`action_pool` 和累计计数。
- 子 workflow 运行时通过 `_subworkflow_context_inputs(run_context, ...)` 传入当前配置，避免全局注册 workflow 复用旧配置。
- `per_question_params` 控制 worker 数、动作探索上限、失败上限、是否给 best guess、action pool 空时重试次数等。
- `tool_map` 决定可用工具；包含 `retrieve` 时，`state_creation` 会进入 retrieval-only 标记。
- 服务端可按配置缓存 key 复用同一个 `DeepSearchAgent` 实例；并行安全依赖 per-run context，而不是实例字段隔离。

## 边界与错误处理

- `init_state` 失败会抛出 `AGENT_INIT_STATE_ERROR`。
- 运行中取消剩余 task 时，会把对应 action 记录到当前 `run_context.action_pool` 为已完成但结果为空。
- action pool 空且重试耗尽时，以 `ACTION_POOL_DEPLETED` 终止。
- 达到时间、动作数或失败数限制时分别以对应 termination 原因返回。
- 不要把 per-run 状态重新挂回 `DeepSearchAgent` 实例字段；否则重叠运行会互相覆盖。
- workflow execute timeout 必须走 `workflow_session_vars`；不要回退到修改全局环境变量。
- 敏感日志模式下不会打印 action、state 等完整内容。

## 测试与验证

- `uv run pytest tests/search_agent/test_deep_search_agent_smoke.py`
- `uv run pytest tests/search_agent/test_integration_search_loop.py`
- `uv run pytest tests/search_agent/test_model_switch_runtime_config.py`
- `uv run pytest tests/search_agent/test_termination.py`
- `uv run pytest tests/search_agent/test_run_action_node.py`
- `uv run pytest tests/search_agent/test_simple_react_toolmap_logging.py`
- `uv run pytest tests/server/test_agent_manager.py`

重叠运行隔离重点覆盖：`test_overlapping_runs_keep_runtime_model_config_isolated`、`test_overlapping_runs_keep_workflow_timeout_isolated`。

## 相关文档

- [Agent 工厂与运行模式](./agent-factory.md)
- [搜索上下文与数据契约](./search-context.md)
- [搜索工具注册与运行时 API 工具](./search-tool-registration.md)
- [DeepSearch Agent 配置组装](../server/deepsearch-agent-config.md)
- [DeepSearch 搜索工作流配置](../config/search-workflow-config.md)
- [DeepSearch 搜索智能体](../algorithm/search-agent.md)
