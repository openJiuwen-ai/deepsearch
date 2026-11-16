# SDK report progress

```python
snapshot = agent.get_progress(conversation_id)
```

Available for Brief and professional reports under `search_mode=research`.
Use the Agent instance executing the report. This synchronous, read-only method can be called from another thread or coroutine.
A newly created Agent cannot query another instance's reports.
Progress starts when the caller consumes `agent.run(...)`, not when it merely constructs the generator.

| Field | Meaning |
| --- | --- |
| `conversation_id` | Report conversation identifier |
| `status` | `running`, `waiting_user_input`, or `cancelling` (cancellation cleanup in progress) |
| `node_id` | Most recently entered main-graph node, or `None` before the first node |
| `updated_at` | Time of the latest node or status change in the executing worker's local timezone, in ISO 8601 format with an explicit offset |
| `collected_info_count` | Result entries in existing collection records; not globally deduplicated across queries or sections |
| `total_sections` | Current outline section count, or `None` before the outline is available (`null` in HTTP) |
| `completed_sections` | Sections that returned normally with nonempty content; failed exits and empty results are excluded |
| `processing_sections` | Up to five sections being processed, containing only `section_id` and `title`; an immutable tuple in the SDK and an array in HTTP |

The return value is an immutable `ProgressSnapshot`. Waiting reports remain queryable; continuing creates a new iteration.
Unknown, finished or other Agents' reports return `None`. Chapter subgraphs do not overwrite the report's main-graph node.
No percentage or remaining-time estimate is returned.

Professional `parallel` reports use `editor_team`; `dependency_driving` uses `dependency_editor_team`;
`hybrid` selects between them through routing. Brief reports use dedicated `brief_*` nodes.
The complete [node_id list by mode](../../../feature/framework/report-progress.md#node_id) is maintained in the feature document.
It lists possible values, not a fixed execution order. Clients should tolerate newly added node names.

Queries only read progress; they do not execute reports or change task state. `updated_at` is not a heartbeat or health signal.

Counts reuse existing business records: professional reports sum query-result entries by section, plan and step;
Brief uses `collection_context.search_results`. These counts do not represent citations, user materials or full-text reads.
Updating the same business record replaces its previous count instead of accumulating notifications or retries.
Continuing after interaction restores summaries from the existing outline, plans, report and Brief state, including on another worker.
Changing the outline version discards the previous version's observations. Dependency research alone does not complete a section;
writing must finish. Brief sections update individually as they finish.
Section IDs are limited to 128 characters and titles to 160; only the first five processing sections in outline order are shown.
Summary-only changes do not update `updated_at`, whose node/status meaning is unchanged.
