# 搜索上下文与数据契约

## 维护范围

本文档覆盖 `search_context.py` 中的 Pydantic 状态模型，以及主图、章节子图和 DeepSearch 子工作流共享的数据契约。

不覆盖字段在 Prompt 中的完整使用方式；Prompt 契约见对应 algorithm 文档。

## 功能目的

搜索上下文定义 workflow 在 session/global_state 中读写的稳定结构，是 framework 节点、算法模块、流式输出和最终响应之间的边界。

## 可见行为

- 主图从 `SearchContext` 初始化用户 query、语言、消息、模板、搜索模式和最终结果。
- 研究报告流程会逐步填充 `research_intent`、`report_type_policy`、`current_report` 和 `final_result`；专业版还会填充 `current_outline`，Brief 则使用独立的 `brief_state`。
- 大纲交互会追加 `outline_interactions`。
- hybrid 大纲路由会写入 `outline_execution_method`，用于固定本次大纲生成、交互接受和写作团队选择。
- 用户反馈处理会更新 `feedback_interaction_count`、`feedback_snapshot_sent` 和 `rewrite_history`。
- `ResearchIntent.source_date_scope` / `content_date_scope` 保存用户明确的双类时间范围（来源发表时间硬门 + 事实时段软分）；collector 用它们生成时间化 query、执行 Tavily `source_date` 的来源日期筛选，并通过 `build_temporal_scope_prompt_context` 传入 `sub_report_markdown` 写作 Prompt（让写作层据时间范围挑选证据）。旧单值 `temporal_scope` 字段 deprecated，仅供旧 state 输入路由（before 校验器路由后 pop，构造后恒为 None）。大纲、规划 Prompt 仍不消费这些字段。
- DeepSearch 搜索流程使用 `State`、`Action`、`Result` 和 `SearchFinalResult` 表达搜索状态与结果。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/section_context.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `tests/workflow/test_workflow_run.py`
- `tests/user_feedback_processor/test_workflow_integration.py`
- `tests/search_agent/test_save_result.py`

## 核心流程

1. `StartNode` 创建 `SearchContext`，并写入 `session.update_global_state({"search_context": ...})`。
2. 主图节点通过 `session.get_global_state("search_context.<field>")` 读取字段。
3. 节点完成后通过同一路径写回当前大纲、报告、告警、异常和最终响应。
4. 章节子图使用 `SectionContext` 接收主图裁剪后的章节状态。
5. DeepSearch 子工作流使用独立 `State`/`Action`/`Result` 模型，不与报告 `SearchContext.current_report` 混用。

## 数据契约与依赖

- `FinalResult.response_content` 是最终正文。
- `FinalResult.citation_messages` 保存全局溯源引用数据。
- `FinalResult.infer_messages` 保存推理链溯源信息。
- `FinalResult.chart_messages` 保存 VLM 图表生成信息。
- `FinalResult.workflow_llm_token_usage` 保存可选 token 统计。
- `Report` 保存总报告文本、子报告、分类内容和溯源校验后的内容。
- `Outline.sections[*].section_focus` 和 `focus_dimensions` 会生成章节局部合同。
- `ResearchIntent` 记录任务类型、比较对象、维度、报告类型、include/exclude URL、禁引文章标题（`exclude_titles`）、域名约束和可空 `source_date_scope`/`content_date_scope` 双时间约束。
- `TemporalScope` 的 `constraint_type` 为 `source_date` 或 `content_date`，并要求 `start_date` /
  `end_date` 至少存在一个包含边界；`source_date_scope`/`content_date_scope` 的 `constraint_type` 必须与字段名一致，不一致置 None 并打 warning。`source_date` 可由 Tavily 原生日期参数和 Tavily 发表日期后置过滤共同执行；
  `content_date` 只通过 collector query 表达事实或数据的时间范围，不按来源发布日期过滤。
- `build_temporal_scope_prompt_context(intent, engine_name=None, scholarly_enabled=False)` 为 collector query、补搜和 `sub_report_markdown` 写作 Prompt 生成六字段：`has_temporal_scope`、`source_date_instruction`、`content_date_instruction`、`temporal_scope_instruction`（两条指令的拼接兼容字段，供旧 Prompt 直接消费）、`temporal_embed_in_query`、`temporal_query_instruction`（合并 embed 指引）；无约束时返回同形六键空值。embed 决策由 `resolve_temporal_embed_in_query` 按 引擎×双 scope 决定搜索词带不带"约束时间词"（content_date 始终带；source_date 在 Tavily 等原生引擎不带、其余引擎带、副引擎启用强制带），主题年份始终放行。intent 支持模型实例与 dict 双形态，统一经 `_resolve_source_date_scope`/`_resolve_content_date_scope` 取值，dict 带旧 `temporal_scope` 键时按 constraint_type 回退路由。`engine_name`/`scholarly_enabled` 默认 None/False 向后兼容。
- `outline_execution_method` 保存本次大纲实际执行方式，当前有效值为 `parallel` 或 `dependency_driving`；缺失或非法时按普通并行大纲处理。
- `brief_state` 是可空的持久化字典，保存 `BriefWorkflowState`：精简大纲、最终证据、已执行 Query 和搜索结果、审阅写作指引、章节正文及核心摘要。它只供 Brief 分支节点读取，不作为专业版章节子图输入。

## 边界与错误处理

- session 中可能保存 dict 或 Pydantic model；节点读取时需要按现有模式兼容两者。
- `current_outline`、`current_report` 允许为空，业务节点必须先判空再访问。
- DeepSearch `State` 使用 `validate_assignment=True`，候选强度限制在 0 到 1。
- 用户可见异常应写入 `final_result.exception_info`，不应只记录日志。

## 测试与验证

- `uv run pytest tests/workflow/test_workflow_run.py`
- `uv run pytest tests/user_feedback_processor/test_workflow_integration.py`
- `uv run pytest tests/search_agent/test_save_result.py`
- 修改 `ResearchIntent` 或报告策略字段时，补充运行查询理解和报告生成相关测试。

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)
- [DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)
- [查询理解](../algorithm/query-understanding.md)
- [Brief 精简版报告工作流](../algorithm/brief-report.md)
