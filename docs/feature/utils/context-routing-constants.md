# 上下文变量、常量与问题路由

## 维护范围

本文档覆盖 workflow 运行时 ContextVar、节点/模型调用常量、搜索引擎枚举和问题路径路由工具。

不覆盖 framework 节点图的连接关系；该部分见 [报告研究主工作流](../framework/research-workflow.md) 和
[DeepSearch 搜索子工作流](../framework/deepsearch-sub-workflows.md)。

## 功能目的

这些工具为跨模块协作提供稳定名称和轻量运行时上下文，避免把 session、LLM、工具实例、搜索实例等对象塞进可序列化 workflow 输入。
问题路由工具则用于判断简单 ReAct 路径或完整 DeepSearch 路径。

## 可见行为

- `session_context`、`model_context`、`llm_context`、`web_search_context`、`local_search_context` 保存每次运行的上下文对象。
- `tool_context` 保存每次运行的工具注册表，避免深拷贝不可复制的客户端对象。
- `NodeId` 集中维护主图、子图、search 和模板流程节点 id。
- `AgentLlmName` 集中维护可用于 LLM 统计和 `agent_llm_timeouts` 的调用点名称。
- 问题路由 LLM 输出 `0` 表示简单 ReAct，输出 `1` 表示完整 DeepSearch。
- 路由失败、空问题或不可解析输出默认返回 `1`。

## 关键代码路径

- `openjiuwen_deepsearch/utils/constants_utils/session_contextvars.py`
- `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`
- `openjiuwen_deepsearch/utils/constants_utils/search_engine_constants.py`
- `openjiuwen_deepsearch/utils/question_model_router.py`
- `openjiuwen_deepsearch/algorithm/prompts/question_model_router.md`
- `tests/utils/test_question_model_router.py`
- `tests/utils/test_llm_utils.py`

## 核心流程

1. Agent 或 BaseNode 在运行入口设置相关 ContextVar。
2. 下游 LLM、搜索工具、流式输出、debug 和统计函数从 ContextVar 读取当前运行状态。
3. 节点和工具代码使用 `NodeId`、`AgentLlmName`、`SearchEngine`、`LocalSearch` 避免硬编码字符串。
4. 问题路由构造 system/user messages，调用 `ainvoke_llm_with_stats`。
5. `parse_bit` 从模型文本中提取第一个 0/1。
6. 无法得到有效 bit 时默认走 DeepSearch。

## 数据契约与依赖

- `cancel_context` 默认值为 `None`，用于取消信号传播。
- `tool_context` 默认值为 `None`，按每次运行设置工具 map。
- 搜索引擎枚举包含 `tavily`、`google`、`xunfei`、`petal`、`bocha`、`jina`、`perplexity`、`serper`。
- 本地搜索枚举包含 `openapi` 和 `native`。
- 问题路由 Prompt 来自 `openjiuwen_deepsearch/algorithm/prompts/question_model_router.md`。

## 边界与错误处理

- ContextVar 未设置时，读取方必须处理 `LookupError` 或默认值。
- 问题路由 LLM 调用异常时记录 warning 并返回 `1`。
- `parse_bit` 只接受文本中出现的 `0` 或 `1`；其他数字不会单独视为合法答案。
- 新增节点或 LLM 调用点时，应同步更新 `NodeId` 或 `AgentLlmName` 以及相关测试。

## 测试与验证

- `uv run pytest tests/utils/test_question_model_router.py`
- `uv run pytest tests/utils/test_llm_utils.py`
- 修改节点常量时，补充运行对应 workflow 测试。

## 相关文档

- [LLM 调用辅助](./llm-invocation-utils.md)
- [报告研究主工作流](../framework/research-workflow.md)
- [DeepSearch 搜索子工作流](../framework/deepsearch-sub-workflows.md)
