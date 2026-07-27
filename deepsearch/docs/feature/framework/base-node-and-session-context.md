# 节点基类与会话上下文

## 维护范围

本文档覆盖 framework 节点公共封装、条件路由构造和 session/contextvar 注入约定。

不覆盖每个业务节点的算法行为；业务流程见 [报告研究主工作流](./research-workflow.md) 和
[DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)。

## 功能目的

节点基类把 openJiuwen `WorkflowComponent` 和 DeepSearch 算法节点隔离开，使业务节点只需要实现输入整理、算法调用和状态回写，同时让
LLM 统计、流式输出和工具调用能读取当前 session。

## 可见行为

- 继承 `BaseNode` 的节点在执行前会设置 `session_context`。
- 节点返回 dict 中的 `next_node` 会被 `init_router` 构造成条件边。
- 未实现 `_pre_handle`、`_do_invoke` 或 `_post_handle` 的节点会抛出不支持异常。
- 节点耗时由 `async_time_logger("invoke")` 统一记录。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/base_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/utils/constants_utils/session_contextvars.py`
- `openjiuwen_deepsearch/utils/debug_utils/node_debug.py`
- `tests/node/test_agent_node.py`
- `tests/workflow/test_workflow_run.py`

## 核心流程

1. openJiuwen workflow 调用节点 `invoke`。
2. `BaseNode.invoke` 把当前 `session` 写入 `session_context`。
3. 子类 `_do_invoke` 读取 session/global_state，调用算法层或子 workflow。
4. 子类 `_post_handle` 更新 session/global_state，并返回 `next_node` 或输出字段。
5. 主 workflow 通过 `init_router` 根据 `next_node` 选择下一节点。

## 数据契约与依赖

- `_pre_handle` 通常把 session 状态转换为普通 dict，避免算法层依赖 openJiuwen session。
- `_do_invoke` 返回 openJiuwen `Output` 兼容 dict。
- `_post_handle` 负责写回 `search_context.*`、`section_context.*` 或 `final_result.*`。
- 条件路由表达式格式为 `${current_node.next_node} == 'target_node'`。

## 边界与错误处理

- `init_router` 只接受单个字符串或字符串列表；其他类型会抛出 `WORKFLOW_ROUTER_INIT_TYPE_ERROR`。
- `BaseNode` 不统一捕获业务异常，业务节点应在自身边界把异常转为状态码、warning 或 `final_result.exception_info`。
- 所有依赖 `session_context` 的公共能力必须在节点或 Agent 入口注入 session 后调用。

## 测试与验证

- `uv run pytest tests/node/test_agent_node.py`
- `uv run pytest tests/workflow/test_workflow_run.py`
- 修改路由时，补充运行对应 workflow 测试并检查 `next_node` 分支覆盖。

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [WorkflowAgent 封装](./workflow-agent.md)
- [搜索上下文与数据契约](./search-context.md)
