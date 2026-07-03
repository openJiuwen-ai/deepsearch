# 报告研究主工作流

## 维护范围

本文档覆盖 `DeepresearchAgent` 和 `DeepresearchDependencyAgent` 组装的报告研究主图，包括节点路由、流式输出、工具/LLM
初始化、报告模板处理、用户交互恢复和会话释放。

章节内部的推理、采集和写作子图见 [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)；用户反馈处理算法见
[用户反馈处理](../algorithm/user-feedback-processor.md)。

## 功能目的

报告研究主工作流把用户 query 转换为结构化意图、大纲、章节子报告、总报告、溯源结果和最终流式响应，是 `research`
模式的核心编排层。

## 可见行为

- `run` 以异步生成器返回 JSON 字符串流式事件。
- 支持普通首次运行，也支持交互中断后的恢复输入。
- `report_template` 支持 base64 文本，解码失败时按明文模板继续。
- 开启 HITL 时先生成澄清问题并等待反馈；关闭 HITL 时直接生成大纲。
- 大纲交互可接受、按评论修改或按用户给定大纲修改。
- 报告生成后按配置执行 VLM 图表、全局溯源、推理链溯源和用户反馈处理。
- `dependency_driving` 模式使用依赖驱动大纲和依赖驱动编辑团队。
- 错误会以 `StreamEvent.ERROR` 输出，随后输出 `ALL END`。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/workflow_agent.py`
- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/workflow_controller.py`
- `tests/workflow/test_workflow_run.py`
- `tests/workflow/test_workflow_llm_usage_lifecycle.py`
- `tests/user_feedback_processor/test_workflow_integration.py`

## 核心流程

1. `DeepresearchAgent.run` 校验参数和 `AgentConfig`。
2. 根据 `llm_config` 创建 LLM 对象并写入 `llm_context`，同时初始化 web/local 搜索工具 context。
3. `StartNode` 初始化 `search_context` 和合并后的 `config`。
4. `IntentRecognitionNode` 识别研究意图、语言、报告类型策略，并在 web/all 模式下做入口预搜索。
5. HITL 开启时进入 `GenerateQuestionsNode` 和 `FeedbackHandlerNode`；否则直接进入 `OutlineNode`。
6. `OutlineNode` 生成大纲，必要时经 `OutlineInteractionNode` 进行多轮修订。
7. 并行模式进入 `EditorTeamNode`；依赖驱动模式进入 `DependencyEditorTeamNode`。
8. `ReporterNode` 生成总报告，随后执行 VLM 图表、全局溯源和推理链溯源。
9. `UserFeedbackProcessorNode` 根据开关进入报告后局部反馈循环，或直接结束。
10. `EndNode` 输出 `final_result` 和 `ALL END`。

## 数据契约与依赖

- `run` 输入：`message`、`conversation_id`、`agent_config`、`report_template`、`interrupt_feedback`。
- workflow 输入 schema 包含 `query`、`thread_id`、`conversation_id`、`report_template`、`interrupt_feedback`、`agent_config`。
- `search_context.final_result` 是最终对外响应载体，包含正文、引用、推理链、图表、LLM token 统计、告警和异常。
- `workflow_feedback_mode=web` 时通过 `session.interact` 进入 openJiuwen 交互恢复链路。
- `stats_info_llm=True` 时，节点会在中断前保存 token 使用量，结束时由 `EndNode` 汇总。

## 边界与错误处理

- `llm_config.general` 缺失时抛出 `LLM_CONFIG_NONE`。
- web/local 搜索引擎注册失败时分别抛出对应搜索实例错误。
- 主图异常会记录接口日志、输出错误事件、释放 Agent session 和 checkpointer session，并重置 contextvar。
- local search 引擎如果提供 `aopen`/`aclose`，运行前后会异步打开和关闭。
- 成功输出 `ALL END` 后会清理搜索 API key 的 `bytearray` secret。
- `finish` 类用户反馈在 `UserFeedbackProcessorNode` 中提前截获并路由到 `EndNode`，不会进入算法层 `execute`。

## 测试与验证

- `uv run pytest tests/workflow/test_workflow_run.py`
- `uv run pytest tests/workflow/test_workflow_llm_usage_lifecycle.py`
- `uv run pytest tests/user_feedback_processor/test_workflow_integration.py`
- 修改大纲交互或依赖驱动路由时，补充运行 `uv run pytest tests/workflow/test_dependency_workflow.py`。

## 相关文档

- [Agent 工厂与运行模式](./agent-factory.md)
- [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)
- [节点基类与会话上下文](./base-node-and-session-context.md)
- [搜索上下文与数据契约](./search-context.md)
- [用户反馈处理](../algorithm/user-feedback-processor.md)
