# DeepSearch 搜索智能体

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/search_agent/`、`search_nodes/`、`search_tools/` 和 `search_index/` 相关的 DeepSearch 搜索智能体能力，包括初始状态生成、action proposal、action pool、工具执行、状态校验和检索工具封装。

本文档不覆盖上层 workflow 节点编排，也不覆盖报告生成阶段的资料分类和正文写作。子能力细节见：

- [Action Pool](./search-agent/action-pool.md)
- [Search Nodes](./search-agent/search-nodes.md)
- [Search Tools](./search-agent/search-tools.md)
- [Search Index](./search-agent/search-index.md)

## 功能目的

DeepSearch 搜索智能体通过“状态初始化 -> 候选 action 生成 -> action 采样与执行 -> 状态验证”的循环探索信息空间，逐步收集回答研究问题所需的证据。它支持 web search/fetch 和固定语料 retrieve 两种工具模式。

## 可见行为

- 初始状态会被解析为 `State`，id 固定为 `0`，depth 为 `0`。
- find action 会生成多个带 score 的 `ActionProposal`，并绑定当前 state、query 和上下文消息。
- action pool 按 proposal score、candidate strength、depth 和配置权重采样待执行 action。
- run action 会根据模式暴露 `web_search` / `web_fetch` 或 `retrieve` 原生工具，并把 LLM 工具调用结果解析为新状态或答案。
- 工具名会先归一化到白名单，未允许的工具名不会执行。

## 关键代码路径

- 搜索 agent 结果解析：`openjiuwen_deepsearch/algorithm/search_agent/deepsearch_agent.py`
- action pool：`openjiuwen_deepsearch/algorithm/search_agent/action_pool.py`
- 初始状态：`openjiuwen_deepsearch/algorithm/search_nodes/initialize_state.py`
- action 发现：`openjiuwen_deepsearch/algorithm/search_nodes/find_action.py`
- action 执行：`openjiuwen_deepsearch/algorithm/search_nodes/run_action.py`
- 状态校验：`openjiuwen_deepsearch/algorithm/search_nodes/validate_new_state.py`
- 工具节点：`openjiuwen_deepsearch/algorithm/search_nodes/tool_node.py`
- web search/fetch 工具：`openjiuwen_deepsearch/algorithm/search_tools/web_search_tool.py`
- retriever 工具：`openjiuwen_deepsearch/algorithm/search_tools/retriever_tool.py`
- 检索索引：`openjiuwen_deepsearch/algorithm/search_index/`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_initialize_state.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_find_action_space.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_run_action.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_validator.md`
- `openjiuwen_deepsearch/algorithm/prompts/deepsearch_verify.md`
- `openjiuwen_deepsearch/algorithm/prompts/simple_react_search.md`

主要测试：

- `tests/search_agent/test_initialize_state.py`
- `tests/search_agent/test_find_action_space.py`
- `tests/search_agent/test_action_pool.py`
- `tests/search_agent/test_run_action.py`
- `tests/search_agent/test_run_action_node.py`
- `tests/search_agent/test_deep_search_agent_smoke.py`
- `tests/search_agent/test_integration_search_loop.py`
- `tests/search_agent/test_termination.py`
- `tests/tools/test_web_search.py`

## 核心流程

1. `run_initialize_state` 调用初始化 Prompt，解析并校验 `State`。
2. `run_find_action_space` 基于 query、state 和已有 result 生成候选 action。
3. `ActionPool` 接收候选 action，按配置进行加权采样、记录运行中和已完成 action。
4. `run_action` 暴露允许的原生工具定义，并把工具调用结果写回消息上下文。
5. LLM 输出被解析为新 `Result` 或新状态。
6. 校验节点判断新状态是否有效、是否已完成、是否需要继续探索。
7. 搜索循环把最终结果交给上层 workflow 和报告生成阶段。

## 数据契约与依赖

关键模型来自 `framework/openjiuwen/agent/search_context.py`：

- `State`
- `Variable`
- `Action`
- `ActionProposal`
- `Result`

运行依赖：

- LLM 配置和 `AgentLlmName`。
- web search/fetch 配置。
- retrieve 模式的本地或固定语料索引。
- action sampling 配置。

## 边界与错误处理

- LLM 输出会剥离 thinking 标签后再解析。
- action proposal 缺少 score 时，会尝试从 direction 文本解析；失败则按解析错误处理。
- find action 多次解析失败后返回空 actions 和错误信息，不应抛出未包装异常。
- native tool 名称必须命中白名单，防止 prompt injection 调用未授权工具。
- action pool 快照写入失败只记录日志，不应阻塞搜索循环。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/search_agent
uv run pytest tests/tools/test_web_search.py
```

如果只改 action pool，可运行：

```bash
uv run pytest tests/search_agent/test_action_pool.py
```

## 相关文档

- [Action Pool](./search-agent/action-pool.md)
- [Search Nodes](./search-agent/search-nodes.md)
- [Search Tools](./search-agent/search-tools.md)
- [Search Index](./search-agent/search-index.md)
- [查询理解](./query-understanding.md)
- [资料采集](./research-collector.md)
- [报告生成](./report-generation.md)
- [Prompt 模板系统](./prompt-template-system.md)
