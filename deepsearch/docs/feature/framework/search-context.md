# 搜索上下文与数据契约

## 维护范围

本文档覆盖 `search_context.py` 中的 Pydantic 状态模型，以及主图、章节子图和 DeepSearch 子工作流共享的数据契约。

不覆盖字段在 Prompt 中的完整使用方式；Prompt 契约见对应 algorithm 文档。

## 功能目的

搜索上下文定义 workflow 在 session/global_state 中读写的稳定结构，是 framework 节点、算法模块、流式输出和最终响应之间的边界。

## 可见行为

- 主图从 `SearchContext` 初始化用户 query、语言、消息、模板、搜索模式和最终结果。
- 研究报告流程会逐步填充 `research_intent`、`report_type_policy`、`current_outline`、`current_report` 和 `final_result`。
- 大纲交互会追加 `outline_interactions`。
- hybrid 大纲路由会写入 `outline_execution_method`，用于固定本次大纲生成、交互接受和写作团队选择。
- 用户反馈处理会更新 `feedback_interaction_count`、`feedback_snapshot_sent` 和 `rewrite_history`。
- `ResearchIntent.temporal_scope` 保存用户明确的时间范围；collector 仅使用它生成时间化 query 和执行 Tavily
  `source_date` 的来源日期筛选，不把该字段传入大纲、规划或报告 Prompt。
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
- `ResearchIntent` 记录任务类型、比较对象、维度、报告类型、include/exclude URL、禁引文章标题（`exclude_titles`）、域名约束和可空 `temporal_scope`。
- `TemporalScope` 的 `constraint_type` 为 `source_date` 或 `content_date`，并要求 `start_date` /
  `end_date` 至少存在一个包含边界。`source_date` 可由支持原生日期参数的搜索引擎（见
  `TEMPORAL_SCOPE_SEARCH_ENGINES`，目前仅 Tavily）和统一后置过滤共同执行；
  `content_date` 只通过 collector query 表达事实或数据的时间范围，不按来源发布日期过滤。
- `resolve_temporal_embed_in_query()` 按（引擎能力, constraint_type）信号矩阵决定 query 是否
  自行携带时间短语：引擎原生过滤已生效的场景（Tavily × `source_date`）返回 False，消除
  双重约束；引擎名取不到可靠值时保守回退 True。
- `build_temporal_scope_prompt_context()` 为 collector query 与补搜 Prompt 生成
  `has_temporal_scope`、`temporal_scope_instruction`、`temporal_embed_in_query`、
  `temporal_open_ended` 和 `temporal_query_instruction`；开放边界（缺 `end_date`）时
  Prompt 禁止 latest/recent 等模糊时间词，要求换算成具体年份或月份。
  不改变其他研究阶段的 Prompt 契约。
- `outline_execution_method` 保存本次大纲实际执行方式，当前有效值为 `parallel` 或 `dependency_driving`；缺失或非法时按普通并行大纲处理。

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
