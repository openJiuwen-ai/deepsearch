# `openjiuwen_deepsearch.framework.openjiuwen.agent.workflow`

## `BaseAgent`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.BaseAgent()
```
Base class for agents.

### `run`
```python
async run(message: str, conversation_id: str, agent_config: dict, report_template: str = "", interrupt_feedback: str = "")
```
Abstract—subclasses must implement (default raises `CustomValueException`).

### `generate_template`
```python
async generate_template(file_name: str, file_stream: str, is_template: bool, agent_config: dict)
```
Build/normalize a report template from base64 **file_stream**; returns `{"status": "success"|"fail", "template_content": str, "error_message": str}` via `TemplateGenerator.generate_template`.

- The decoded source file must be no larger than `50 MB`.
- If `is_template=False` and the upload is a PDF, it supports up to `512` pages.
- If `is_template=False` and the upload is a DOCX, the uncompressed package must stay within `50 MB`, and `word/document.xml` must stay within `8 MB`.
- The normalized/parsed Markdown output is capped at `5 MB`.

---

## `DeepresearchAgent`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchAgent()
```
Parallel research workflow.

### `run`
```python
async run(message: Optional[str] = None, conversation_id: Optional[str] = None, agent_config: Optional[dict] = None, report_template: str = "", interrupt_feedback: str = "")
```
Streams JSON chunks (`AsyncGenerator[str]`) for normal execution, HITL resume, outline HITL, and post-report edits. Returns a **dict** when `interrupt_feedback="cancel"`.

- **agent_config** validated with `AgentConfig.model_validate`.
- **interrupt_feedback**: `""` (normal SSE stream), `"accepted"` (HITL continue), `"cancel"` (JSON cancel response), `"revise_comment"` / `"revise_outline"` (outline HITL).
- **report_template**: if base64, decoded automatically; decode errors fall back to raw string.

Behavior highlights: initializes LLM + search tools; `native` local search requires non-empty `knowledge_base_configs`; wraps interactive interrupts; `ALL END` completes and clears context; cancel works in-process and with Redis checkpointer; when `user_feedback_processor_enable=True`, flow enters `UserFeedbackProcessorNode` after `SourceTracerInferNode` for post-report local editing.

### `register_web_search_tool` / `_register_local_search_tool`
Static helpers to register custom/web or local tools; native local requires `knowledge_base_configs`. Research also registers the built-in PubMed and arXiv secondary engines, while DeepSearch registers only its configured active web engine.

---

## `DeepresearchDependencyAgent`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchDependencyAgent(DeepresearchAgent)
```
Dependency-driven variant when `execution_method="dependency_driving"`. Same `run` contract; main path `DependencyOutlineNode` → `DependencyOutlineInteractionNode` → `DependencyEditorTeamNode` → `ReporterNode`; `DependencyEditorTeamNode` schedules dependency layers so prior writing and next-layer reasoning can overlap.

---

## `DeepresearchIntentHybridAgent`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchIntentHybridAgent(DeepresearchAgent)
```
Hybrid outline-routing research workflow used when `execution_method="hybrid"`. It reuses the normal `OutlineNode` and `OutlineInteractionNode`; `IntentRecognitionNode` calls the outline-mode router LLM and writes `search_context.outline_execution_method`. A `parallel` result continues to `EditorTeamNode`, while a `dependency_driving` result continues to `DependencyEditorTeamNode`.

---

## `DeepSearchAgent`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent()
```
Search-mode agent when `search_mode="search"`: action-space search with parallel `state_creation` workers, `search_fetch` or `retrieve` tools, and a single JSON chunk from `run`. Full API: [`deepsearch_agent`](./deepsearch_agent.md).

---

## `validate_generate_template_params` / `validate_run_params`
Validate inputs for `generate_template` / `run`.

## `parse_endnode_content`
```python
parse_endnode_content(chunk: CustomSchema) -> dict | None
```
Parses EndNode output: if JSON includes `exception_info`, returns that dict; otherwise returns an empty dict.
