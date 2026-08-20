# 报告研究主工作流

## 维护范围

本文档覆盖 `DeepresearchAgent`、`DeepresearchDependencyAgent` 和 `DeepresearchIntentHybridAgent` 组装的报告研究主图，包括节点路由、流式输出、工具/LLM
初始化、报告模板处理、用户交互恢复、大纲模式路由和会话释放。

章节内部的推理、采集和写作子图见 [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)；用户反馈处理算法见
[用户反馈处理](../algorithm/user-feedback-processor.md)。

## 功能目的

报告研究主工作流把用户 query 转换为结构化意图，并按报告类型进入专业版或 Brief 精简版分支，最终输出溯源后的流式响应，是 `research` 模式的核心编排层。

## 可见行为

- `run` 以异步生成器返回 JSON 字符串流式事件。
- 支持普通首次运行，也支持交互中断后的恢复输入。
- `report_template` 支持 base64 文本，解码失败时按明文模板继续。
- 开启 HITL 时先生成澄清问题并等待反馈；关闭 HITL 时直接生成大纲。
- 大纲交互可接受、按评论修改或按用户给定大纲修改。
- 报告生成后按配置执行 VLM 图表、全局溯源、推理链溯源和用户反馈处理。
- `dependency_driving` 模式使用依赖驱动大纲和依赖驱动编辑团队。
- `hybrid` 模式在意图识别节点中调用 LLM 判断本次 query 应走普通大纲还是依赖驱动大纲，并把结果写入 `search_context.outline_execution_method`。
- `report_type=brief` 时进入自包含的 Brief 支路：报告级采集、证据审阅、至多一次补搜、并行章节写作、核心摘要、可选 Mermaid 和最终引用校验；不会进入专业版 EditorTeam、VLM、推理链溯源或报告后反馈。
- `final_result.response_content` 非空时，`EndNode` 会依据 `search_context.language` 在正文末尾追加中文或英文的 AI 生成标注；部分失败报告仍保留错误事件与 `exception_info`。
- 错误会以 `StreamEvent.ERROR` 输出，随后输出 `ALL END`。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/algorithm/query_understanding/outline_mode_router.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/workflow_agent.py`
- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/workflow_controller.py`
- `openjiuwen_deepsearch/algorithm/prompts/outline_mode_router.md`
- `tests/workflow/test_workflow_run.py`
- `tests/workflow/test_workflow_llm_usage_lifecycle.py`
- `tests/user_feedback_processor/test_workflow_integration.py`

## 核心流程

1. `DeepresearchAgent.run` 校验参数和 `AgentConfig`。
2. 根据 `llm_config` 创建 LLM 对象并写入 `llm_context`，同时初始化 web/local 搜索工具 context。
3. `StartNode` 初始化 `search_context` 和合并后的 `config`。
4. `IntentRecognitionNode` 识别研究意图、语言、报告类型策略，并在 web/all 模式下做入口预搜索；当 `execution_method=hybrid` 时，同一节点额外调用大纲模式 router LLM。
5. hybrid router 的结果写入 `search_context.outline_execution_method`，取值为 `parallel` 或 `dependency_driving`；非 hybrid 模式写入对应的固定执行结果。
6. HITL 开启时进入 `GenerateQuestionsNode` 和 `FeedbackHandlerNode`；否则按报告类型直接进入 `BriefOutlineNode` 或专业版 `OutlineNode`。
7. Brief 进入 `BriefOutline → BriefInfoCollector → BriefEvidenceReviewer`；无阻断缺口直接并行写作，有阻断缺口仅返回采集节点补搜一次，随后直接写作。
8. Brief 的章节、摘要和可选 Mermaid 完成后，经 `SourceTracerNode(NodeId.END)` 校验引用并结束。
9. 专业版 `OutlineNode` 按 `search_context.outline_execution_method` 选择普通或依赖驱动大纲 prompt/tool schema，并可经 `OutlineInteractionNode` 修订。
10. 专业版的 `parallel` 进入 `EditorTeamNode`，`dependency_driving` 进入 `DependencyEditorTeamNode`；随后由 `ReporterNode`、VLM、全局溯源、推理链溯源和用户反馈处理完成。
11. `EndNode` 为非空报告正文追加 AI 生成标注后，输出 `final_result` 和 `ALL END`。

## 数据契约与依赖

- `run` 输入：`message`、`conversation_id`、`agent_config`、`report_template`、`interrupt_feedback`。
- workflow 输入 schema 包含 `query`、`thread_id`、`conversation_id`、`report_template`、`interrupt_feedback`、`agent_config`。
- `agent_config.execution_method=hybrid` 是外部执行模式入口；`search_context.outline_execution_method` 是本次大纲模式的实际路由结果。
- `outline_mode_router.md` 的输出契约只允许 `parallel` 或 `dependency_driving`，不能输出解释、标点或其他文本。
- `search_context.final_result` 是最终对外响应载体，包含正文、引用、推理链、图表、LLM token 统计、告警和异常。
- Brief 的过程状态位于 `search_context.brief_state`；该分支不写入专业版 `current_outline` 或章节 Plan。
- `workflow_feedback_mode=web` 时通过 `session.interact` 进入 openJiuwen 交互恢复链路。
- `stats_info_llm=True` 时，节点会在中断前保存 token 使用量，结束时由 `EndNode` 汇总。

## 边界与错误处理

- `llm_config.general` 缺失时抛出 `LLM_CONFIG_NONE`。
- web/local 搜索引擎注册失败时分别抛出对应搜索实例错误。
- 主图异常会记录接口日志、输出错误事件、释放 Agent session 和 checkpointer session，并重置 contextvar。
- local search 引擎如果提供 `aopen`/`aclose`，运行前后会异步打开和关闭。
- 成功输出 `ALL END` 后会清理搜索 API key 的 `bytearray` secret。
- `finish` 类用户反馈在 `UserFeedbackProcessorNode` 中提前截获并路由到 `EndNode`，不会进入算法层 `execute`。
- hybrid router 仅在 `search_mode=research` 且 `execution_method=hybrid` 的研究主流程中生效；`search` 和 `react` 模式不使用该路由。
- hybrid router 调用失败、缺少 LLM entry、query 为空或输出非法时，默认回退为 `parallel`。
- 大纲交互中的 `revise_comment` 和 `revise_outline` 不会重新触发 hybrid router，继续复用首次写入的 `search_context.outline_execution_method`。
- Brief 不消费 `outline_execution_method`，因此并行、依赖驱动和 hybrid 的 Brief 后续流程相同；hybrid 的入口路由结果只带来额外的入口决策调用。

## 测试与验证

- `uv run pytest tests/workflow/test_workflow_run.py`
- `uv run pytest tests/workflow/test_workflow_llm_usage_lifecycle.py`
- `uv run pytest tests/user_feedback_processor/test_workflow_integration.py`
- 修改大纲交互或依赖驱动路由时，补充运行 `uv run pytest tests/workflow/test_dependency_workflow.py`。
- 修改 hybrid 大纲路由时，补充运行 `uv run pytest tests/algorithm/query_understanding/test_outline_mode_router.py tests/node/test_agent_node.py tests/node/test_outline_interaction_node.py tests/workflow/test_create_agent.py tests/workflow/test_dependency_workflow.py tests/server/test_deepsearch_run.py`。
- 修改 Brief 路由或节点时，运行 `uv run pytest tests/brief_report`。

## 相关文档

- [Agent 工厂与运行模式](./agent-factory.md)
- [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)
- [节点基类与会话上下文](./base-node-and-session-context.md)
- [搜索上下文与数据契约](./search-context.md)
- [Brief 精简版报告工作流](../algorithm/brief-report.md)
- [用户反馈处理](../algorithm/user-feedback-processor.md)
