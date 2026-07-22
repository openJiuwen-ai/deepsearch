# DeepSearch 搜索工作流配置

## 维护范围

本文档覆盖 `SearchWorkflowConfig`、`PerQuestionParams`、`ActionSamplingConfig`、`MilvusConfig`、`InitStateAgentConfig`、`FindActionAgentConfig`、`StateCreationAgentConfig`、`RetrievalSettingsConfig` 和 `ValidatorAgentConfig`。

不覆盖搜索节点的 Prompt 内容、Action Pool 细节或工具实现；这些属于 algorithm/framework 对应文档。

## 功能目的

搜索工作流配置把 DeepSearch 单问题搜索循环的时间、并发、工具模式、action 采样、检索、状态创建和校验策略做成可传入的运行契约，使 `search_mode=search` 和 `search_mode=react` 能在不同 benchmark、生产运行和调试场景间切换参数。

## 可见行为

- `AgentConfig.search_workflow_per_question_params` 控制单个问题的并发、时间限制、工具模式、探索上限、连续失败上限和超时 best-guess 行为。
- `AgentConfig.search_workflow_milvus_config` 只在 `tool_map=retrieve` 时用于构造 retrieve 工具。
- `ServiceConfig.search_workflow` 提供 init state、find action、state creation 以及 state creation 内部 validator 的子工作流配置。
- DeepSearch agent 接收顶层运行输入中的 `service_config.search_workflow` 时会尝试解析为 `SearchWorkflowConfig`；缺失或非法时退回默认配置并记录 warning。
- `SearchStartNode` 会把当前请求的 `agent_config` 和 `search_config` 注入子工作流，避免复用全局 workflow 时带入上一轮模型配置。
- `init_state_workflow`、`find_action_workflow` 和 `state_creation_workflow` 会为各自 LLM 配置补默认 timeout、max_tries 和 thinking 标签策略。

## 关键代码路径

- `openjiuwen_deepsearch/config/config.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/algorithm/search_agent/action_pool.py`
- `openjiuwen_deepsearch/algorithm/search_nodes/initialize_state.py`
- `openjiuwen_deepsearch/algorithm/search_nodes/find_action.py`
- `openjiuwen_deepsearch/algorithm/search_nodes/run_action.py`
- `openjiuwen_deepsearch/algorithm/search_nodes/validate_new_state.py`
- `openjiuwen_deepsearch/algorithm/search_nodes/utils.py`
- `tests/search_agent/test_config_matrix.py`
- `tests/search_agent/test_model_switch_runtime_config.py`
- `tests/search_agent/test_deep_search_agent_smoke.py`
- `tests/search_agent/test_integration_search_loop.py`
- `tests/search_agent/test_termination.py`

## 核心流程

1. `DeepSearchAgent.run` 从顶层 `agent_config` 中分离可选的 `service_config` 和 `gold_answer`。
2. 顶层配置经 `AgentConfig` 校验后写入 `self.agent_config`。
3. `service_config.search_workflow` 经 `SearchWorkflowConfig` 校验后写入 `self.search_config`，失败时使用默认值。
4. `search_workflow_per_question_params` 设置 action 循环的超时、并发和工具模式。
5. 子工作流运行前通过 `_subworkflow_context_inputs` 传入当前 `agent_config` 和 `search_config`。
6. `SearchStartNode` 按 `workflow_name` 选择 init state、find action 或 state creation 配置，并补齐当前 `general` LLM。
7. 搜索节点读取合并后的配置执行状态初始化、action 发现、工具执行和状态校验。

## 数据契约与依赖

- `PerQuestionParams.tool_map` 取值为 `search_fetch` 或 `retrieve`。
- `search_fetch` 模式的 web search provider 从 `web_search_engine_config` 初始化并注册到 context；web fetch provider 从 `web_fetch_provider_config` 显式选择，当前只支持 `provider_name="jina"`。
- 搜索运行结束后会清零 `web_search_engine_config.search_api_key` 和 `web_fetch_provider_config.api_key`。
- `retrieve` 模式依赖 `MilvusConfig` 和 embedding 配置；使用后会清零 `embedder_api_key`。
- `actions_explored_limit=0` 表示不限制；大于 0 时达到该数量后终止，默认值 200 是实际探索上限。
- `fail_limit=0` 表示不限制连续失败次数。
- `answer_mode_top_k<=1` 表示找到第一个答案就返回；大于 1 时收集候选答案并按 candidate score 选择。
- `ActionSamplingConfig` 控制 depth 权重、唯一状态提升和随机采样。
- `StateCreationAgentConfig.context_limit_reached_strategy` 控制上下文过长时失败、降低检索量或删除工具消息后重试。
- `RetrievalSettingsConfig.mode` 支持 dense、sparse、hybrid。

## 边界与错误处理

- `tool_map` 非法时返回 `PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR`。
- `service_config.search_workflow` 缺失或解析失败不会中断运行，DeepSearch 使用默认 `SearchWorkflowConfig`。
- 子工作流名称不属于 init state、find action 或 state creation 时，返回 `WORKFLOW_TYPE_NOT_EXIST_ERROR`。
- 子工作流必须从当前请求读取模型配置；修改全局 workflow 注册逻辑时要防止模型槽位串用。
- retrieve 模式新增配置字段时，需要同步匿名化和 secret 清零逻辑。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/search_agent
```

针对性验证：

```bash
uv run pytest tests/search_agent/test_config_matrix.py
uv run pytest tests/search_agent/test_model_switch_runtime_config.py
uv run pytest tests/search_agent/test_deep_search_agent_smoke.py
uv run pytest tests/search_agent/test_termination.py
```

## 相关文档

- [Agent 与服务运行配置](./agent-and-service-config.md)
- [DeepSearch 搜索智能体](../algorithm/search-agent.md)
- [Action Pool](../algorithm/search-agent/action-pool.md)
- [Search Nodes](../algorithm/search-agent/search-nodes.md)
- [Search Tools](../algorithm/search-agent/search-tools.md)
- [DeepSearch 网页抓取 Provider 注册](../framework/web-fetch-provider-registry.md)
- [DeepSearch 搜索子工作流](../framework/deepsearch-sub-workflows.md)
