# 章节推理与写作子工作流

## 维护范围

本文档覆盖报告研究模式中按章节执行的子工作流，包括并行章节流程和依赖驱动章节流程。

信息采集 ReAct 子图的内部工具循环见 [信息采集子图](./info-collector-subgraph.md)；报告生成算法细节见
[报告生成](../algorithm/report-generation.md)。

## 功能目的

章节子工作流把主图大纲拆成可并发或按依赖执行的章节任务。每个章节独立规划、采集资料、撰写子报告并生成章节级溯源信息，最终汇总为
`current_report.sub_reports`。

## 可见行为

- 并行模式下，每个大纲章节都会启动一个 `editor_team` 子图。
- 依赖驱动模式下，会按章节 `parent_ids` 分层执行，父章节完成后把背景知识传给子章节。
- 子图会流式输出 plan、资料采集和子报告节点消息，并带上 `section_idx`、`plan_idx`、`step_idx`。
- 如果缺失大纲、章节列表为空或所有子报告为空，主流程会记录 warning/exception 并结束。
- 每个章节会构造 `section_local_contract`，约束章节只展开自身负责的分析维度。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/editor_team_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_reasoning_team_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_writing_team_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/section_context.py`
- `tests/workflow/test_sub_workflow.py`
- `tests/workflow/test_dependency_workflow.py`
- `tests/workflow/test_dependency_editor_team_node.py`
- `tests/framework/test_background_knowledge.py`

## 核心流程

1. `EditorTeamNode` 从主 session 读取大纲、模板、历史报告、配置和意图约束。
2. 每个章节创建 `SubReport`，并把章节输入转换成 `SectionContext`。
3. 并行模式子图执行 `PlanReasoning -> InfoCollector -> PlanReasoning` 循环，资料足够后进入 `SubReporter`。
4. `SubReporter` 生成章节文本、摘要、sidecar 和分类文档。
5. `SubSourceTracer` 生成章节级溯源数据，随后 `SectionEndNode` 返回章节状态。
6. `EditorTeamNode` 合并所有章节结果，更新 `current_report`、历史大纲和历史报告。
7. 依赖驱动模式先通过 `DependencyPlanReasoningNode` 和 `DependencyInfoCollectorNode` 生成父章节步骤知识，再按层执行写作子图。

## 数据契约与依赖

- 主图输入依赖 `SearchContext.current_outline.sections`。
- 子图状态是 `SectionContext` 或 `SectionReasoningContext`。
- 章节输出通过 `SubReportContent` 写回 `SubReport.content`。
- `section_local_contract` 来自 `section_focus` 和 `focus_dimensions`，供 planner、collector 和 reporter Prompt 消费。
- `report_type_policy`、`research_intent`、`audience_role`、`tone` 会从主图传入章节子图。

## 边界与错误处理

- 缺失大纲时使用 `EDITORTEAM_MANAGER_MISSING_OUTLINE`。
- 大纲没有章节时使用 `EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION`。
- 所有子报告为空时使用 `EDITORTEAM_MANAGER_EMPTY_SUB_REPORT`。
- 章节达到最大规划次数且没有资料时使用 `SECTION_INFOS_EMPTY`。
- 依赖驱动中若步骤依赖无法满足，会记录 `INFO_COLLECTING_EMPTY` warning 并结束当前计划。

## 测试与验证

- `uv run pytest tests/workflow/test_sub_workflow.py`
- `uv run pytest tests/workflow/test_dependency_workflow.py`
- `uv run pytest tests/workflow/test_dependency_editor_team_node.py`
- `uv run pytest tests/workflow/test_dependency_reasoning_nodes.py`
- `uv run pytest tests/workflow/test_dependency_writing_nodes.py`
- `uv run pytest tests/framework/test_background_knowledge.py`

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [信息采集子图](./info-collector-subgraph.md)
- [搜索上下文与数据契约](./search-context.md)
- [报告生成](../algorithm/report-generation.md)
- [资料采集](../algorithm/research-collector.md)
