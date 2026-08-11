# `openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes`

Main-graph and key subgraph nodes (aligned with current code).

## Main graph nodes

### `StartNode`
```python
class StartNode(Start)
```
Workflow entry: validate/default inputs, init `SearchContext` (`query`, `session_id`, `messages`, `search_mode`, `report_template`), merge `agent_config` + `service_config` into runtime `config`, set `thread_id` and `interrupt_feedback`.

### `IntentRecognitionNode`
```python
class IntentRecognitionNode(BaseNode)
```
Report intent recognition and language detection via `classify_and_recognize_intent`; normalizes locale (`zh-CN` / `en-US`); executes initial web search; on failure sets `final_result.exception_info` and stops.

### `GenerateQuestionsNode`
```python
class GenerateQuestionsNode(BaseNode)
```
HITL clarifying questions via `query_interpreter` with `workflow_max_gen_question_retry_num` retries; success → `search_context.questions`; failure → `exception_info`.

### `FeedbackHandlerNode`
```python
class FeedbackHandlerNode(BaseNode)
```
Reads user feedback (`workflow_feedback_mode` `cmd`/`web`); `FINISH_TASK` ends run; invalid input → `exception_info`.

### `OutlineNode`
```python
class OutlineNode(BaseNode)
```
Outline generation: `report_template` present uses `outliner_template` prompt else `outliner`; retries via `outliner_max_generate_outline_retry_num`; streams outline to `search_context.current_outline`.

### `DependencyOutlineNode`
```python
class DependencyOutlineNode(OutlineNode)
```
Dependency-aware outline via `dep_driving_outliner`; same retry/stream behavior as `OutlineNode`.

### `OutlineInteractionNode`
```python
class OutlineInteractionNode(BaseNode)
```
Outline HITL: if `outline_interaction_enabled` is off, or if rounds are greater than or equal to `outline_interaction_max_rounds`, the node routes to `EditorTeamNode` or `DependencyEditorTeamNode` according to `search_context.outline_execution_method`. It reads feedback (`cmd`/`web`) as JSON:

```json
{
  "interrupt_feedback": "accepted/revise_comment/revise_outline",
  "feedback": "User text: comments for revise_comment, or new outline for revise_outline"
}
```

Actions: `accepted` → `EditorTeamNode` or `DependencyEditorTeamNode` according to `search_context.outline_execution_method`; `revise_comment` / `revise_outline` → `OutlineNode`; history in `search_context.outline_interactions`.

### `DependencyOutlineInteractionNode`
```python
class DependencyOutlineInteractionNode(OutlineInteractionNode)
```
Same as parent; on `accepted` routes to `DependencyEditorTeamNode` instead of `EditorTeamNode`.

### `EditorTeamNode`
```python
class EditorTeamNode(BaseNode)
```
(`editor_team_manager_node.py`) Runs concurrent sub-workflows and forwards streamed subgraph output.

### `DependencyEditorTeamNode`
```python
class DependencyEditorTeamNode(EditorTeamNode)
```
Dependency-layer pipeline: per layer, parallelize previous-layer writing with current-layer reasoning; merges subgraph streams.

### `ReporterNode`
```python
class ReporterNode(BaseNode)
```
Final report via `Reporter.generate_report`; failures → `exception_info`; success → `search_context.report` and `all_classified_contents`.

### `VLMChartGeneratorNode`
```python
class VLMChartGeneratorNode(BaseNode)
```
**VLMChartGeneratorNode** handles VLM iterative chart generation.

**Functions**:
- If `vlm_chart_generator_enable` is disabled, skip this node.
- If `vlm_chart_generator_enable` is enabled, VLM model configuration must be provided or LLM is general model; otherwise the system disables this module and skips it.
- The system selects chart insertion positions, generates charts, and performs corresponding chart optimization.
- Writes to `final_result.chart_messages`.
- Chart generation errors are written to `exception_info`.

### `SourceTracerNode`
```python
class SourceTracerNode(BaseNode)
```
**SourceTracerNode** handles provenance tracing and verification.

**Functions**:
- Skip when `source_tracer_research_trace_source_switch` is disabled.
- Run citation verification after preprocessing and generate citation information.
- Write results into `final_result.response_content` and `citation_messages`.
- Insert stable `[checked_citation:id]` markers into the report body and return matching citation metadata, so the frontend can render and continue interaction based on the latest `final_result`.
- Write failures into `exception_info`.

### `UserFeedbackProcessorNode`
```python
class UserFeedbackProcessorNode(BaseNode)
```
**UserFeedbackProcessorNode** handles iterative local rewrite requests and selected-content fact verification after report generation is complete.

**Functions**:
- Decide whether to enable post-report local editing based on `user_feedback_processor_enable`.
- On first entry, send a full `final_result` snapshot to the frontend and use `search_context.feedback_snapshot_sent` to ensure it is sent only once.
- Read JSON user feedback and support `expand`, `shorten`, `polish`, `supplementary_search`, `new_task`, `truth_verification`, `sync`, and `finish`.
- Parse and validate rewrite payload fields such as `action`, `rewrite_scope`, `selected_text`, and offsets.
- Support both `selected_only` and `selected_and_related` as rewrite scopes for `supplementary_search`.
- Treat `truth_verification` as read-only: validate the selection, return a JSON verification result in `SUMMARY_RESPONSE`, do not update `final_result.response_content` or `search_context.rewrite_history`, but do consume `feedback_interaction_count`.
- Return a lightweight ack for `sync`, without consuming `feedback_interaction_count`; successful sync appends a rewrite-history record only when the full report content actually changes.
- Call `UserFeedbackProcessor` to complete the local rewrite and update `final_result.response_content`.
- When `source_tracer_research_trace_source_switch` is enabled, normal rewrite, `supplementary_search`, and `new_task` actions run diff-aware local source tracing on changed spans; unchanged spans keep their existing citations, and newly traced citations update `citation_messages` and append reference entries at the end of the report.
- For normal rewrite, `supplementary_search`, and `new_task` actions, maintain `search_context.feedback_interaction_count` and `search_context.rewrite_history`, including action type, rewrite scope, and actual replacement range.
- The rewrite path no longer maintains extra frontend offset mappings; `sync` only synchronizes the report body and does not trigger local source tracing.
- Keep only the latest 10 `sync` history records; unchanged `sync` requests do not create history records.
- Apply `user_feedback_processor_max_interactions` only to non-`sync` actions; end the flow after receiving `finish`.

### `SourceTracerInferNode`
```python
class SourceTracerInferNode(BaseNode):
```
Skips if `source_tracer_infer_switch` is off; builds provenance reasoning artifacts → `final_result.infer_messages`; failures → `exception_info`.

### `EndNode`
```python
class EndNode(End)
```
When `final_result.response_content` is non-empty, appends an AI-generation notice in the language selected by `search_context.language`, then emits `final_result` JSON and `"ALL END"`; error events and `exception_info` remain unchanged.

---

## Editor-team subgraph (`reasoning_writing_graph/editor_team_nodes.py`)

`SectionStartNode` → `ResearchPlanReasoningNode` → (`InfoCollectorNode` → `ResearchPlanReasoningNode`)* → `SubReporterNode` → `SubSourceTracerNode` → `SectionEndNode`.

---

## Collector subgraph (`collector_graph/`)

`StartNode` → `GenerateQueryNode` → `InfoRetrievalNode` → `SupervisorNode` → (loop)* → `SummaryNode` → `GraphEndNode` → `End`.

---

## Dependency reasoning subgraph (`dependency_reasoning_team_nodes.py`)

`SectionReasoningStartNode` → `DependencyPlanReasoningNode` → (`DependencyInfoCollectorNode` → `DependencyPlanReasoningNode`)* → `SectionReasoningEndNode`.

---

## Dependency writing subgraph (`dependency_writing_team_nodes.py`)

`SectionWritingStartNode` → `SubReporterNode` → `SubSourceTracerNode` → `SectionEndNode`.

---

## Execution sketches

### Parallel main graph
```
StartNode -> IntentRecognitionNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> OutlineNode
-> [OutlineInteractionNode -> OutlineNode]* -> EditorTeamNode -> ReporterNode -> SourceTracerNode -> EndNode
-> SourceTracerInferNode -> UserFeedbackProcessorNode -> EndNode
```

### Dependency-driven main graph
```text
StartNode -> IntentRecognitionNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> DependencyOutlineNode
-> [DependencyOutlineInteractionNode -> DependencyOutlineNode]*
-> DependencyEditorTeamNode -> ReporterNode -> SourceTracerNode
-> SourceTracerInferNode -> UserFeedbackProcessorNode -> EndNode
```

`DependencyEditorTeamNode` pipelines dependency layers (“previous writing + current reasoning” in parallel per layer).

### Hybrid outline-routing main graph
```text
StartNode -> IntentRecognitionNode -> [GenerateQuestionsNode -> FeedbackHandlerNode] -> OutlineNode
-> [OutlineInteractionNode -> OutlineNode]*
-> EditorTeamNode / DependencyEditorTeamNode -> ReporterNode -> SourceTracerNode
-> SourceTracerInferNode -> UserFeedbackProcessorNode -> EndNode
```

When `execution_method="hybrid"`, `IntentRecognitionNode` calls the outline-mode router LLM and stores the selected branch in `search_context.outline_execution_method`. `OutlineNode` and `OutlineInteractionNode` reuse the normal node classes, then select the normal or dependency-driven outline path and writing team from that session field.

### Editor-team subgraph
```
SectionStartNode -> ResearchPlanReasoningNode -> [InfoCollectorNode -> ResearchPlanReasoningNode]*
-> SubReporterNode -> SubSourceTracerNode -> SectionEndNode
```

### Collector subgraph
```
StartNode -> GenerateQueryNode -> InfoRetrievalNode -> SupervisorNode
-> [InfoRetrievalNode -> SupervisorNode]* -> SummaryNode -> GraphEndNode -> End
```

### Dependency reasoning subgraph
```
SectionReasoningStartNode -> DependencyPlanReasoningNode -> [DependencyInfoCollectorNode -> DependencyPlanReasoningNode]*
-> SectionReasoningEndNode
```

### Dependency writing subgraph
```
SectionWritingStartNode -> SubReporterNode -> SubSourceTracerNode -> SectionEndNode
```
