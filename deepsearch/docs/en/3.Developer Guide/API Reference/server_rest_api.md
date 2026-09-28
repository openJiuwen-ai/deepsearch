# DeepSearch Main REST API

The server OpenAPI is `/api/docs`; this page provides stable caller contracts and examples. The internal telemetry API is in `deepsearch_rest_api.md`.

## Run reports: `POST /api/v1/agent/deepsearch/run/`

The body is `DeepSearchRequest`. Required fields are `space_id`, `conversation_id`, and `message`; supply at least one of `web_search_config` or `local_search_config` and valid `llm_config`. Common options include `outliner_max_section_num` (default 10, range 1–15), `workflow_human_in_the_loop`, `report_type` (`brief`/`professional`), and `metadata`.

```json
{"space_id":"space-1","conversation_id":"conv-1","message":"Analyze the NEV market","llm_config":{"general":{"model_name":"model","model_type":"openai","base_url":"https://example.com/v1","api_key":"***"}},"web_search_config":{"search_engine_name":"tavily","search_api_key":"***"}}
```

The response is an SSE JSON event stream. `event="waiting_user_input"` is a HITL pause: reuse `conversation_id` and send feedback with `interrupt_feedback` (`accepted`, `revise_outline`, or `revise_comment`). `interrupt_feedback="cancel"` returns cancellation status instead of a stream. Validation errors return 422; unauthorized space/resource access returns an appropriate 4xx response.

## Brief: `POST /api/v1/agent/deepsearch/run_brief/`

The request, SSE, HITL, and error contract match `/run/`, but the server always sets `report_type` to `brief` and ignores a caller-supplied report type.

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
| Create/update/delete | `POST /create`, `/update`, `/delete`; all include `space_id`; creation requires `name`, `embed_model_config`, and `llm_config`. |
| Upload and process | `POST /upload` (multipart: `space_id`, `kb_id`, file); `/process` and `/task/progress` follow indexing. |
| Search and list | `POST /search` (`space_id`, `query`, paging) and `/list`. |
| Document management | `POST /documents/status`, `/documents/list`, `/documents/update`, `/documents/delete`. |

Knowledge bases and documents are isolated by `space_id`. `/api/docs` is authoritative for fields; see [run streaming](../../../feature/server/deepsearch-run-streaming.md) for SSE/HITL and cleanup.
