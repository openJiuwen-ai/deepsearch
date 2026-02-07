# jiuwen_deepsearch.framework.jiuwen.agent.main_graph_nodes

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

### class EntryNode
```python
class EntryNode(BaseNode)
```
**EntryNode** 负责查询分类与路由。

**功能**：
- 调用 `classify_query` 判断是否进入深搜。
- 写入流式输出事件（START/MESSAGE/DONE）。
- 统一语言标识（`zh-CN` / `en-US`）。
- 若不进入深搜，直接写入 `final_result.response_content` 并结束。

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

### class EditorTeamNode
```python
class EditorTeamNode(BaseNode)
```
编辑团队子图管理节点（定义在 `editor_team_manager_node.py`）。

**功能**：
- 构建并发子工作流并汇聚结果。
- 透传子图流式输出。

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

### class SourceTracerNode
```python
class SourceTracerNode(BaseNode)
```
**SourceTracerNode** 负责溯源与校验。

**功能**：
- 若 `source_tracer_research_trace_source_switch` 关闭则跳过。
- 预处理后调用校验逻辑，生成引用信息。
- 写入 `final_result.response_content` 与 `citation_messages`。
- 校验失败时写入 `exception_info`。

---

### class SourceTracerInferNode
```python
class SourceTracerInferNode(BaseNode)
```
**SourceTracerInferNode** 负责溯源推理（并行流程默认执行）。

**功能**：
- 若 `source_tracer_source_tracer_infer_switch` 关闭则直接返回。
- 调用 `SourceTracerInfer.run` 生成推理信息。
- 写入 `final_result.response_content` 与 `infer_messages`。
- 异常时写入 `exception_info`。

---

### class EndNode
```python
class EndNode(End)
```
**EndNode** 输出最终结果与结束标记。

**功能**：
- 将 `final_result` 以 JSON 输出。
- 输出 `"ALL END"` 标记。

---

## 编辑团队子图节点（Editor Team Subgraph Nodes）

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

## 节点执行流程

### 主工作流（并行）
```
StartNode -> EntryNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> OutlineNode
-> EditorTeamNode -> ReporterNode -> SourceTracerNode -> SourceTracerInferNode -> EndNode
```

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
