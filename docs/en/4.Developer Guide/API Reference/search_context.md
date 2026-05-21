# `openjiuwen_deepsearch.framework.openjiuwen.agent.search_context`

## `Message`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Message(name: str = "", role: str, content: str)
```
Chat message model: **name** (optional), **role** (`user` / `system` / `assistant`), **content** (required).

## `StepType`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.StepType(str, Enum)
```
Enum: **INFO_COLLECTING** = `"info_collecting"`.

## `RetrievalQuery`
Per-step query bundle: **query**, **description**, **doc_infos** (`Optional[List[Dict]]`).

## `Step`
Plan step: **id**, **type** (`StepType`), **title**, **description**, **parent_ids**, **relationships**, **background_knowledge**, **retrieval_queries**, **step_result**, **evaluation**.

## `Plan`
Section plan: **id**, **language** (default `zh-CN`), **title**, **thought**, **is_research_completed**, **steps**, **background_knowledge**.

## `Section`
Outline section: **id**, **title**, **description**, **is_core_section** (default `False`), **parent_ids**, **relationships**, **plans**.

## `Outline`
**id**, **language** (default `zh-CN`), **thought**, **title**, **sections**.

## `OutlineInteraction`
Outline HITL record: **feedback**, **interaction_mode** (`revise_comment` / `revise_outline`), **outline_before**.

## `SubReport` / `SubReportContent`
Sub-report shell: **section_id**, **section_task**, **background_knowledge** (dependency mode may hold `{"section_id", "content_summary"}` parents), **content** (`SubReportContent` with **classified_content**, **sub_report_content**, **sub_report_content_summary**, **sub_report_trace_source_datas**).

## `Report`
Aggregated report: **report_task**, **report_template**, **sub_reports**, **report_content**, **all_classified_contents**, **merged_trace_source_datas**, **checked_trace_source_report_content**, **checked_trace_source_datas**.

## `FinalResult`
**response_content**, **citation_messages**, **infer_messages**, **chart_messages**, **exception_info**, **warning_info**.

- `infer_messages` stores source-tracing graph payloads. Report export reads `html_base64` and writes standalone HTML resources.
- `chart_messages` stores VLM chart payloads. Report export reads `base64` and writes image resources.

## `ResearchIntent`
Structured report-generation constraints parsed from the user query.

**Fields**:

- **section_count**: Desired section count. Only positive integers are kept.
- **audience_role**: Target reader role.
- **tone**: Writing tone as a stable English enum value, such as `formal` or `analytical`.
- **report_type**: Report type as a stable English enum value, such as `standard` or `deep_research`.
- **include_url**: URLs the user explicitly wants to include. Default value: `[]`.
- **exclude_url**: URLs the user wants to exclude. Default value: `[]`.
- **include_domains**: Site domains specified by the user. Default value: `[]`.
- **exclude_domains**: Site domains excluded by the user. Default value: `[]`.

## `SearchContext`
**Fields**:

- **session_id**: Session ID.
- **original_query**: Original user query.
- **research_query**: Research topic after intent recognition, used for retrieval and planning.
- **research_intent**: Structured report constraints. Default value: empty `ResearchIntent`.
- **messages**: Conversation messages.
- **language**: Language. Default value: `zh-CN`.
- **report_template**: Report template.
- **search_mode**: Search mode. Default value: `research`.
- **entry_search_results**: Entry-node pre-search results.
- **questions**: Clarification questions.
- **user_feedback**: User feedback.
- **outline_interactions**: Outline interaction history.
- **outline_executed_num**: Number of outline executions.
- **current_outline**: Current outline.
- **history_outlines**: Historical outlines.
- **report_generated_num**: Default value: `0`.
- **current_report**: Current report.
- **history_reports**: Historical reports.
- **final_result**: Final result.
- **feedback_interaction_count**: Number of post-report local editing interactions. Default value: `0`.
- **feedback_snapshot_sent**: Whether the initial feedback snapshot has already been pushed to the frontend. Default value: `False`.
- **rewrite_history**: Local rewrite history.
- **debug_pre_node**: Previous debug node. Default value: `""`.
