# openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes

本文档描述 DeepSearch 工作流的主图节点与子图节点，内容与当前代码保持一致。

## 主图节点（Main Graph Nodes）

### class StartNode
```python
class StartNode(Start)
```
**StartNode** 是工作流起始节点。

**功能**：
- 校验并补齐输入默认值。
- 初始化 `SearchContext`：`query`、`session_id`、`messages`、`search_mode`、`report_template`。
- 合并 `agent_config` 与 `service_config` 写入 runtime `config`。
- 写入 `thread_id` 与 `interrupt_feedback` 到 runtime 配置。

---

### class IntentRecognitionNode
```python
class IntentRecognitionNode(BaseNode)
```
**IntentRecognitionNode** 负责报告意图识别与语言检测。

**功能**：
- 调用 `classify_and_recognize_intent` 解析用户查询意图。
- 检测用户语言并统一语言标识（`zh-CN` / `en-US`）。
- 执行网络搜索获取初始结果。
- 失败或异常写入 `final_result.exception_info` 并结束。

---

### class GenerateQuestionsNode
```python
class GenerateQuestionsNode(BaseNode)
```
**GenerateQuestionsNode** 生成澄清问题（HITL）。

**功能**：
- 调用 `query_interpreter`，按 `workflow_max_gen_question_retry_num` 重试。
- 成功写入 `search_context.questions`。
- 失败或异常写入 `final_result.exception_info` 并结束。

---

### class FeedbackHandlerNode
```python
class FeedbackHandlerNode(BaseNode)
```
**FeedbackHandlerNode** 读取用户反馈。

**功能**：
- 根据 `workflow_feedback_mode` 读取反馈（`cmd`/`web`）。
- 处理 `FINISH_TASK` 直接结束。
- 反馈无效或模式错误时写入 `exception_info` 并结束。

---

### class OutlineNode
```python
class OutlineNode(BaseNode)
```
**OutlineNode** 生成报告大纲。

**功能**：
- `report_template` 存在时使用 `outliner_template` 提示词，否则使用 `outliner`。
- 按 `outliner_max_generate_outline_retry_num` 重试。
- 成功时流式输出大纲并写入 `search_context.current_outline`。

---

### class DependencyOutlineNode
```python
class DependencyOutlineNode(OutlineNode)
```
**DependencyOutlineNode** 生成依赖驱动工作流报告大纲

**功能**：
- 基于 `dep_driving_outliner` 提示词生成带依赖关系的大纲。
- 按 `outliner_max_generate_outline_retry_num` 重试。
- 成功时流式输出大纲并写入 `search_context.current_outline`。

---

### class OutlineInteractionNode
```python
class OutlineInteractionNode(BaseNode)
```
**OutlineInteractionNode** 大纲交互节点，接收用户反馈并决定后续流程。

**功能**：
- 检查 `outline_interaction_enabled` 配置，禁用时根据 `search_context.outline_execution_method` 跳转到 `EditorTeamNode` 或 `DependencyEditorTeamNode`。
- 检查当前交互轮次，达到 `outline_interaction_max_rounds` 时通知用户，并根据 `search_context.outline_execution_method` 跳转到对应写作团队。
- 通过 `workflow_feedback_mode`（`cmd`/`web`）获取用户输入, 用户输入需要为如下json格式：
```json
{
  "interrupt_feedback": "accepted/revise_comment/revise_outline",
  "feedback": "用户的反馈，action为revise_comment时为修改意见，action为revise_outline时为新的大纲格式"
}
```
- 支持三种用户反馈动作：
  - `accepted`：用户接受大纲，根据 `search_context.outline_execution_method` 跳转到 `EditorTeamNode` 或 `DependencyEditorTeamNode`
  - `revise_comment`：用户提供修改意见，跳转到 `OutlineNode` 重新生成大纲
  - `revise_outline`：用户直接修改大纲，跳转到 `OutlineNode` 重新生成大纲
- 保存交互记录到 `search_context.outline_interactions`。

---

### class DependencyOutlineInteractionNode
```python
class DependencyOutlineInteractionNode(OutlineInteractionNode)
```
**DependencyOutlineInteractionNode** 依赖驱动工作流的大纲交互节点。

**功能**：
- 继承自 `OutlineInteractionNode`，交互逻辑与父类相同。
- 区别在于：用户接受大纲时，始终跳转到 `DependencyEditorTeamNode`。
- 修改评论时，仍然跳转到 `OutlineNode`。

---

### class EditorTeamNode
```python
class EditorTeamNode(BaseNode)
```
编辑团队子图管理节点（定义在 `editor_team_manager_node.py`）。

**功能**：
- 构建并发子工作流并汇聚结果。
- 透传子图流式输出。

---

### class DependencyEditorTeamNode
```python
class DependencyEditorTeamNode(EditorTeamNode)
```
**DependencyEditorTeamNode** 依赖驱动工作流编辑团队节点（定义在 `editor_team_manager_node.py`）。

**功能**：
- 按依赖层级流水线并行执行：每层同时执行「上一层的写作」与「本层的推理」（如 1 推理完成后，1 的写作与 2、3 的推理并行）。
- 基于前置依赖关系构建推理子工作流与写作子工作流并汇聚结果。
- 透传子图流式输出信息收集与报告内容。

---

### class ReporterNode
```python
class ReporterNode(BaseNode)
```
**ReporterNode** 生成最终报告内容。

**功能**：
- 调用 `Reporter.generate_report`。
- 失败时写入 `exception_info` 并结束。
- 成功时写入 `search_context.report` 与 `all_classified_contents`。

---

### class VLMChartGeneratorNode
```python
class VLMChartGeneratorNode(BaseNode)
```
**VLMChartGeneratorNode** 负责vlm迭代式图表生成。

**功能**：
- 若 `vlm_chart_generator_enable` 关闭则跳过。
- 若 `vlm_chart_generator_enable` 开启, 必须传入vlm模型配置，或者llm是通用模型（可处理多模态任务），否则系统关闭该模块开关，跳过模块。
- 系统选择图表插入位置生成图表并完成相应图表优化。
- 写入 `final_result.chart_messages`。
- 图表生成错误会写入 `exception_info`。

---

### class SourceTracerNode
```python
class SourceTracerNode(BaseNode)
```
**SourceTracerNode** 负责溯源与校验。

**功能**：
- 若 `source_tracer_research_trace_source_switch` 关闭则跳过。
- 预处理后调用校验逻辑，生成引用信息。
- 写入 `final_result.response_content` 与 `citation_messages`。
- 引用结果会在报告正文中写入稳定的 `[checked_citation:id]` 标记，并同步返回对应的 citation metadata，供前端按最新 `final_result` 渲染与后续交互。
- 校验失败时写入 `exception_info`。


---

### class UserFeedbackProcessorNode
```python
class UserFeedbackProcessorNode(BaseNode)
```
**UserFeedbackProcessorNode** 在报告生成完成后，处理用户对局部文本的迭代改写请求，以及选中内容的真实性核验。

**功能**：
- 根据 `user_feedback_processor_enable` 决定是否启用报告后局部优化。
- 首次进入时先向前端发送完整的 `final_result` 快照，并通过 `search_context.feedback_snapshot_sent` 保证只发送一次。
- 读取用户 JSON 反馈，支持 `expand`、`shorten`、`polish`、`supplementary_search`、`new_task`、`truth_verification`、`sync`、`finish`。
- 对改写类动作解析并校验 `action`、`rewrite_scope`、`selected_text`、偏移量等字段。
- `supplementary_search` 支持 `selected_only` 与 `selected_and_related` 两种改写范围。
- `truth_verification` 为只读动作：校验选区后通过 `SUMMARY_RESPONSE` 返回 JSON 核验结果，不更新 `final_result.response_content`，不写入 `search_context.rewrite_history`，但会消耗 `feedback_interaction_count`。
- `sync` 会以轻量 ack 回传整篇报告更新结果，不消耗 `feedback_interaction_count`；只有整篇报告内容实际变化时才会追加一条 `rewrite_history` 记录。
- 调用 `UserFeedbackProcessor` 完成局部改写并更新 `final_result.response_content`。
- 当 `source_tracer_research_trace_source_switch` 开启时，普通 rewrite、`supplementary_search` 和 `new_task` 会对变化片段执行差异感知局部溯源；未变化片段保留原引用，新增引用会同步更新 `citation_messages` 并在文末追加参考文献。
- 普通 rewrite / supplementary_search / new_task 会维护 `search_context.feedback_interaction_count` 与 `search_context.rewrite_history`，记录动作类型、改写范围和实际替换区间。
- 改写链路不再额外维护前端偏移映射；`sync` 仅同步正文，不触发局部溯源。
- `sync` 历史仅保留最近 10 条；内容未变化的 `sync` 不会新增历史记录。
- 只有非 `sync` 动作会受 `user_feedback_processor_max_interactions` 约束；收到 `finish` 后结束流程。

---

### class SourceTracerInferNode
```python
class SourceTracerInferNode(BaseNode):
```
**SourceTracerInferNode** 负责溯源推理。

**功能**：
- 若`source_tracer_infer_switch`关闭则跳过。
- 系统自动选择需要进行溯源推理的报告内容，生成对应的溯源推理图。
- 写入 `final_result.infer_messages`。
- 当溯源推理失败时写入`exception_info`。

---

### class EndNode
```python
class EndNode(End)
```
**EndNode** 输出最终结果与结束标记。

**功能**：
- 将 `final_result` 以 JSON 输出。
- `final_result.response_content` 非空时，根据 `search_context.language` 在报告末尾追加对应语言的 AI 生成标注；错误事件和 `exception_info` 保持不变。
- 输出 `"ALL END"` 标记。

---

## 编辑团队子图节点（Reasoning Writing Subgraph Nodes）

定义在 `reasoning_writing_graph/editor_team_nodes.py`：

- `SectionStartNode`：初始化 `section_context`。
- `ResearchPlanReasoningNode`：生成章节计划并决定后续路径。
- `InfoCollectorNode`：执行信息收集子图。
- `SubReporterNode`：生成子报告。
- `SubSourceTracerNode`：对子报告进行溯源标记。
- `SectionEndNode`：返回子图结果。

---

## 信息收集子图节点（Info Collector Subgraph Nodes）

定义在 `collector_graph/graph_builder.py` 与 `collector_graph/info_collector.py`：

- `StartNode`：初始化 `collector_context`。
- `GenerateQueryNode`：生成初始查询列表。
- `InfoRetrievalNode`：执行 ReAct 搜索与信息整理。
- `SupervisorNode`：评估信息是否足够并决定是否继续。
- `SummaryNode`：生成信息收集总结。
- `GraphEndNode`：输出 `info_summary` 并回写消息。

---

## 依赖驱动工作流推理子图节点（Dependency Driven Reasoning Subgraph Nodes）

定义在 `reasoning_writing_graph/dependency_reasoning_team_nodes.py`：

- `SectionReasoningStartNode`：初始化 `section_context`。
- `DependencyPlanReasoningNode`：基于前置依赖生成章节计划并决定后续路径。
- `DependencyInfoCollectorNode`：执行依赖驱动信息收集子图。
- `SectionReasoningEndNode`：返回信息收集结果与章节计划。

---

## 依赖驱动工作流子报告撰写子图节点（Dependency Driven Writing Subgraph Nodes）

定义在 `reasoning_writing_graph/dependency_writing_team_nodes.py`：

- `SectionWritingStartNode`：初始化 `section_context`。
- `SubReporterNode`：生成子报告。
- `SubSourceTracerNode`：对子报告进行溯源标记。
- `SectionEndNode`：返回子图结果。

---

## 节点执行流程

### 主工作流（并行）
```
StartNode -> IntentRecognitionNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> OutlineNode
-> [OutlineInteractionNode -> OutlineNode]* -> EditorTeamNode -> ReporterNode -> SourceTracerNode -> EndNode
-> SourceTracerInferNode -> UserFeedbackProcessorNode -> EndNode
```

### 主工作流（依赖驱动）
```text
StartNode -> IntentRecognitionNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> DependencyOutlineNode
-> [DependencyOutlineInteractionNode -> DependencyOutlineNode]*
-> DependencyEditorTeamNode -> ReporterNode -> SourceTracerNode
-> SourceTracerInferNode -> UserFeedbackProcessorNode -> EndNode
```

说明：`DependencyEditorTeamNode` 会在内部同时编排依赖驱动的推理子图与写作子图，
按章节依赖层级执行“上一层写作 + 本层推理”的流水线并行调度。

### 主工作流（混合大纲路由）
```text
StartNode -> IntentRecognitionNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> OutlineNode
-> [OutlineInteractionNode -> OutlineNode]*
-> EditorTeamNode / DependencyEditorTeamNode -> ReporterNode -> SourceTracerNode
-> SourceTracerInferNode -> UserFeedbackProcessorNode -> EndNode
```

说明：当 `execution_method="hybrid"` 时，`IntentRecognitionNode` 调用大纲模式 router LLM，并把结果写入 `search_context.outline_execution_method`。`OutlineNode` 与 `OutlineInteractionNode` 复用普通节点实现，再按该字段选择普通大纲/依赖驱动大纲以及后续写作团队。

### 编辑团队子图
```
SectionStartNode -> ResearchPlanReasoningNode -> [InfoCollectorNode -> ResearchPlanReasoningNode]*
-> SubReporterNode -> SubSourceTracerNode -> SectionEndNode
```

### 信息收集子图
```
StartNode -> GenerateQueryNode -> InfoRetrievalNode -> SupervisorNode
-> [InfoRetrievalNode -> SupervisorNode]* -> SummaryNode -> GraphEndNode -> End
```

### 依赖驱动工作流编辑团队子图
```
SectionReasoningStartNode -> DependencyPlanReasoningNode -> [DependencyInfoCollectorNode -> DependencyPlanReasoningNode]*
-> SectionReasoningEndNode
```

### 依赖驱动工作流子报告撰写子图
```
SectionWritingStartNode -> SubReporterNode -> SubSourceTracerNode -> SectionEndNode
```
