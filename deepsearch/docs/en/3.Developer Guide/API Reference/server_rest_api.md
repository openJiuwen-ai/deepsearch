# DeepSearch Main REST API

The server OpenAPI is `/api/docs`; this page provides stable caller contracts and examples. The internal telemetry API is in `deepsearch_rest_api.md`.

## Run reports: `POST /api/v1/agent/deepsearch/run/`

The body is `DeepSearchRequest`. Required fields are `space_id`, `conversation_id`, `message`, and valid `llm_config`. Search configuration depends on `info_collector_search_method`: `web` (the default) requires `web_search_config`, `local` requires `local_search_config`, and `all` requires both. Common options include `outliner_max_section_num` (default 10, range 1–15), `workflow_human_in_the_loop`, `report_type` (`brief`/`professional`/`null`), and `metadata`. HTTP defaults `outline_interaction_enabled` to `false` (SDK `AgentConfig` defaults it to `true`).

For web search, first save an engine with the `POST /api/v1/agent/deepsearch/web_search/` endpoint below, then pass its response `web_search_engine_id` as `web_search_config.web_search_config_id`; the server loads the engine name, key, and URL by that ID.

```json
{"space_id":"space-1","conversation_id":"conv-1","message":"Analyze the NEV market","llm_config":{"general":{"model_name":"model","model_type":"openai","base_url":"https://example.com/v1","api_key":"***"}},"web_search_config":{"web_search_config_id":42,"max_web_search_results":5}}
```

The response is an SSE JSON event stream. `event="waiting_user_input"` is a HITL pause: reuse `conversation_id` and send feedback with `interrupt_feedback` (`accepted`, `revise_outline`, or `revise_comment`). `interrupt_feedback="cancel"` returns cancellation status instead of a stream. Validation errors return 422; unauthorized space/resource access returns an appropriate 4xx response.

## Brief: `POST /api/v1/agent/deepsearch/run_brief/`

The request, SSE, HITL, and error contract match `/run/`, but the server always sets `report_type` to `brief` and ignores a caller-supplied report type.

## Report progress: `GET /api/v1/agent/deepsearch/run/progress`

Required query parameters: nonempty `space_id` and `conversation_id` (1–128 ASCII letters, digits, underscores or hyphens).
No body or model credentials are needed. The endpoint observes active reports under `search_mode=research`, including Brief and all three professional execution modes.

```json
{
  "conversation_id": "report-1",
  "status": "running",
  "node_id": "editor_team",
  "updated_at": "2026-10-22T14:30:00+08:00",
  "collected_info_count": 36,
  "total_sections": 5,
  "completed_sections": 2,
  "processing_sections": [
    {
      "section_id": "3",
      "title": "市场规模与竞争格局"
    },
    {
      "section_id": "4",
      "title": "技术发展趋势"
    }
  ]
}
```

`status` is `running`, `waiting_user_input` or `cancelling` (cancellation cleanup in progress). `node_id` is the most recently entered main-graph node,
or `null` during initialization. Chapter subgraphs do not overwrite it. `updated_at` is the latest node/status update time, in the executing worker's local timezone,
matching its business logs. It uses ISO 8601 with an explicit offset and is not a heartbeat.
Nodes may repeat or revisit earlier positions. No percentage or remaining-time estimate is returned.
Professional `parallel` reports use `editor_team`; `dependency_driving` uses `dependency_editor_team`;
`hybrid` selects between them through routing. Brief reports use dedicated `brief_*` nodes.
See the complete [node_id list by mode](../../../feature/framework/report-progress.md#node_id).
The list is not a fixed execution order; clients should tolerate newly added nodes.

Additional summary fields:

| Field | Meaning |
| --- | --- |
| `collected_info_count` | Result entries in existing collection records; not globally deduplicated across queries or sections |
| `total_sections` | Current outline section count, or `None` before the outline is available (`null` in HTTP) |
| `completed_sections` | Sections that returned normally with nonempty content; failed exits and empty results are excluded |
| `processing_sections` | Up to five sections being processed, containing only `section_id` and `title`; an immutable tuple in the SDK and an array in HTTP |

Counts reuse existing business records: professional reports sum query-result entries by section, plan and step;
Brief uses `collection_context.search_results`. These counts do not represent citations, user materials or full-text reads.
Updating the same business record replaces its previous count instead of accumulating notifications or retries.
Continuing after interaction restores summaries from the existing outline, plans, report and Brief state, including on another worker.
Changing the outline version discards the previous version's observations. Dependency research alone does not complete a section;
writing must finish. Brief sections update individually as they finish.
Section IDs are limited to 128 characters and titles to 160; only the first five processing sections in outline order are shown.
Summary-only changes do not update `updated_at`, whose node/status meaning is unchanged.

HTTP 200 returns status and collection/section summaries; 404 means no active record; 422 indicates invalid parameters; 503 means Redis is unavailable or its record is corrupt.
Waiting reports remain queryable after an SSE disconnect. Records are removed once the report finishes; no history is kept.
Redis workers share snapshots with a 30-second TTL. Changes are coalesced at most once per second; unchanged records are only renewed every five seconds by default. Queries may return 404 before the initial asynchronous publication.
Publishing failures do not block execution; expired or superseded records are not recreated by the old publisher. Other backends query only the local process.
Redis deployments wait up to two seconds before execution to prepare a progress round. If allocation cannot be confirmed, execution and SDK-local observation continue, but shared publishing is disabled for that round. Publication failures after confirmed allocation are retried; late initial publications from older rounds cannot overwrite newer snapshots or recreate removed ones.
Queries only read progress; they do not execute reports, change task state, or renew shared records.
See [SDK progress](research_progress.md) and [feature boundaries](../../../feature/framework/report-progress.md).

## Web-search engines: `/api/v1/agent/deepsearch/web_search/`

| Method | Path | Key request fields | Success response |
| --- | --- | --- | --- |
| POST | `/` | `space_id`, `search_engine_name`, `search_api_key`; optional URL, extension, active flag | `code`, `msg`, `web_search_engine_id` |
| GET | `/{space_id}/{web_search_engine_id}` | Path parameters | Engine name, URL, extension, active state |
| GET | `/{space_id}` | Path parameter | `data` engine list |
| PUT | `/` | `space_id`, engine ID, fields to change | `code`, `msg`, ID |
| DELETE | `/{space_id}/{web_search_engine_id}` | Path parameters | `code`, `msg` |
| POST | `/{space_id}/{web_search_engine_id}` | Path parameters; optional `query` body | `datas` search results |

## Templates: `/api/v1/agent/deepsearch/template`

Import requires `space_id`, `file_name`, Base64 `file_stream`, `is_template`, `template_name`, `template_desc`, and `llm_config`; the response contains `code`, `msg`, and `template_id`. `GET /{space_id}` lists templates; `GET /{space_id}/{template_id}` returns Base64 content; `PUT /` updates with space ID, template ID, content, name, and description; `DELETE /{space_id}/{template_id}` deletes. Validation errors return 422.

## Knowledge bases: `/api/kb`

| Operation | Path and key request fields |
| --- | --- |
| Create/update/delete | `POST /create`, `/update`, `/delete`; all include `space_id`; creation requires `name`, `embed_model_config`, and `llm_config`; update additionally requires `kb_id`, `name`, `desc`, `embed_model_config`, and `llm_config`. |
| Upload and process | `POST /upload` (multipart: `space_id`, `kb_id`, file); `/process` and `/task/progress` follow indexing. |
| Search and list | `POST /search` filters knowledge bases by name or description (case-insensitive; it does not search document bodies); request fields are `space_id`, `query`, `page` (default 1), and `page_size` (default 10, maximum 100). `/list` lists knowledge bases. |
| Document management | `POST /documents/status`, `/documents/list`, `/documents/update`, `/documents/delete`. |

Knowledge bases and documents are isolated by `space_id`. `/api/docs` is authoritative for fields; see [run streaming](../../../feature/server/deepsearch-run-streaming.md) for SSE/HITL and cleanup.
