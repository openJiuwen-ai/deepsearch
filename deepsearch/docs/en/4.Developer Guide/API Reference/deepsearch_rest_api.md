## Telemetry Backend API

The telemetry backend is implemented by `server.telemetry_event_server` and runs as a FastAPI app (default: `http://127.0.0.1:8089`). It supports telemetry ingestion, background DeepSearch run execution, run cancellation, and JSONL log reads.

### Starting the backend server

Run the this command from project root:

```bash
uv run python -m server.telemetry_event_server
```

If you bind with a wildcard host (for example `--host 0.0.0.0`), the server now uses loopback as the internal telemetry callback base by default. You can still override this explicitly with `--public-base-url`.


### `GET /health`
Lightweight liveness endpoint (plain text response).

**Response**:
- `200 OK`, body is plain text:
  - `ok; append JSONL to <path>` when file logging is enabled.
  - `ok (no JSONL file)` when `--no-jsonl` is used.

**Example output**:
```text
ok; append JSONL to /Users/dev/deepsearch/output/telemetry_logs/telemetry.jsonl
```

### `GET /`
Root liveness endpoint; same behavior as `GET /health`.

**Response**:
- `200 OK` plain text.

**Example output**:
```text
ok; append JSONL to /Users/dev/deepsearch/output/telemetry_logs/telemetry.jsonl
```

### `POST /events`
Telemetry ingest endpoint (path configurable via `--path`, default `/events`).

Accepts one JSON object per request and appends it as one JSONL line when file logging is enabled.

**Request body**:
- JSON object (commonly includes fields from telemetry emitter such as `event`, `run_id`, `seq`, `ts`, `payload`).
- Empty body is accepted and treated as `{}`.

**Response**:
- `204 No Content` on success.
- `400 Bad Request` if body is invalid JSON or not a JSON object.

**Example request body (well-formed event JSON)**:
```json
{
  "event": "run_started",
  "run_id": "f23e4567-e89b-12d3-a456-426614174000",
  "seq": 1,
  "ts": "2026-05-07T13:15:01.123Z",
  "source": "main.run_jiuwen_workflow",
  "action_id": null,
  "payload": {
    "query": "Who was the president of the former country whose capital is known as the white city?",
    "search_mode": "search"
  }
}
```

**Example output (success)**:
```text
HTTP/1.1 204 No Content
```

**Example output (invalid body)**:
```json
{
  "detail": "Bad Request"
}
```

### `POST /runs`
Starts a background DeepSearch graph run (`search` or `react`, not `research`) via `main.run_jiuwen_workflow`.

**Request body** (`CreateSearchRunRequest`):
- `query` (`str`, required): user question.
- `llm` (`object`, required): includes required `model_name`, `base_url`, `api_key`.
- `search_mode` (`"search" | "react"`, default `"search"`):
  - `search`: DeepSearch multi-step graph workflow.
  - `react`: simple ReAct loop (single LLM + tool calls) with the same tool family.
- `enable_question_router` (`bool`, default from `Config().agent_config`): when `true` and `search_mode="search"`, an LLM router can switch simple questions to `react` (0→react, 1→DeepSearch) because running the full DeepSearch pipeline would be overkill for those cases.
- `run_id` (`str | null`, optional): if omitted, server generates UUID.
- `conversation_id` (`str | null`, optional): if omitted, server generates UUID (API lifecycle correlation id).
- `tool_map` (`"search_fetch" | "retrieve"`, default from `PerQuestionParams`).
- `web_fetch_provider_config` and `web_search_engine_config` (both required when `tool_map="search_fetch"`). `web_fetch_provider_config.provider_name` must be set explicitly; v1 supports `jina`.
- `milvus` (`object`, optional): Milvus/embedder settings; embedder key/base URL required when `tool_map="retrieve"`.
- `search_workflow_per_question_params` (`object`, optional): shallow overrides validated against `PerQuestionParams`.

**Response**:
- `201 Created` with JSON:
  - `run_id`: actual run id.
  - `status`: `"started"`.
  - `conversation_id`: API lifecycle conversation id.

**Errors**:
- `409 Conflict`: `run_id` already in progress.
- `422 Unprocessable Entity`: validation error (for example missing resolved search/fetch config, unsupported fetch provider, or invalid per-question overrides).

**Telemetry event “chunks” (current DeepSearch telemetry JSON types)**:

| Event type | Meaning |
| --- | --- |
| `run_started` | Workflow run has started (includes run-level context such as query preview/search mode). |
| `action_proposals_created` | `find_action` produced candidate actions (or failed and recorded error context). |
| `action_pool_snapshot` | Snapshot of action pool (`pending` / `running` / `completed`) was persisted. |
| `state_created` | New state(s) were created (for example from initial state or action patch). |
| `messages_updated` | Message history snapshot was updated (LLM turn/tool turn/final phase). |
| `action_result_saved` | One action execution result was written to result artifacts. |
| `search_final_result` | Final `SearchFinalResult` was produced and finalized. |

**Example request body**:
```json
{
  "search_mode": "search",
  "enable_question_router": true,
  "run_id": "f23e4567-e89b-12d3-a456-426614174000",
  "query": "Who was the president of the former country whose capital is known as the white city?",
  "conversation_id": "53e6d4e4-65bd-49ad-9a67-a0b6138df111",
  "llm": {
    "model_name": "gpt-4o-mini",
    "model_type": "openai",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-***",
    "hyper_parameters": {
      "temperature": 0.2,
      "top_p": 1.0
    },
    "extension": {}
  },
  "tool_map": "search_fetch",
  "web_fetch_provider_config": {
    "provider_name": "jina",
    "api_key": "jina_***",
    "base_url": "",
    "extension": {}
  },
  "web_search_engine_config": {
    "search_engine_name": "serper",
    "search_api_key": "serper_***"
  },
  "search_workflow_per_question_params": {
    "time_limit": 300,
    "max_workers": 2
  }
}
```

**Example output (201)**:
```json
{
  "run_id": "f23e4567-e89b-12d3-a456-426614174000",
  "status": "started",
  "conversation_id": "53e6d4e4-65bd-49ad-9a67-a0b6138df111"
}
```

**Example output (409)**:
```json
{
  "detail": "run_id already in progress"
}
```

### `POST /runs/{run_id}/cancel`
Cancels an in-flight run.

**Path params**:
- `run_id` (`str`): run identifier to cancel.

**Response**:
- `204 No Content` when cancellation signal is accepted.
- `404 Not Found` if run is unknown or already finished.

**Example output (204)**:
```text
HTTP/1.1 204 No Content
```

**Example output (404)**:
```json
{
  "detail": "unknown or finished run_id"
}
```

### `GET /telemetry/recent`
Returns the last N telemetry events from JSONL (optionally filtered by `run_id`).

**Query params**:
- `n` (`int`, required): number of events to return; clamped to `[1, 10000]`.
- `run_id` (`str`, optional): filter events for one run.

**Response**:
- `200 OK` JSON:
  - `items`: list of telemetry event objects.
  - `count`: number of returned items.

**Example output**:
```json
{
  "items": [
    {
      "event": "run_started",
      "run_id": "f23e4567-e89b-12d3-a456-426614174000",
      "seq": 1,
      "ts": "2026-05-07T13:15:01.123Z",
      "source": "main.run_jiuwen_workflow",
      "action_id": null,
      "payload": {
        "query": "Who was the president of the former country whose capital is known as the white city?",
        "search_mode": "search"
      }
    },
    {
      "event": "node_completed",
      "run_id": "f23e4567-e89b-12d3-a456-426614174000",
      "seq": 2,
      "ts": "2026-05-07T13:15:04.008Z",
      "source": "openjiuwen.agent.main_nodes",
      "action_id": "9f203ecb-4465-44ca-9f67-7c6a3f3021e1",
      "payload": {
        "node_name": "find_action",
        "duration_ms": 612,
        "proposals_count": 2
      }
    },
    {
      "event": "run_completed",
      "run_id": "f23e4567-e89b-12d3-a456-426614174000",
      "seq": 3,
      "ts": "2026-05-07T13:15:12.334Z",
      "source": "server.telemetry_event_server._run_search_workflow",
      "action_id": null,
      "payload": {
        "conversation_id": "53e6d4e4-65bd-49ad-9a67-a0b6138df111"
      }
    }
  ],
  "count": 3
}
```

### `GET /telemetry/range`
Returns telemetry events by `run_id` and inclusive sequence range.

**Query params**:
- `run_id` (`str`, required).
- `start_seq` (`int`, required).
- `end_seq` (`int`, required, must be `>= start_seq`).

**Response**:
- `200 OK` JSON:
  - `items`: matching telemetry events.
  - `count`: number of returned items.

**Errors**:
- `422 Unprocessable Entity` when `start_seq > end_seq`.

**Example output (`run_id=f23e4567-e89b-12d3-a456-426614174000&start_seq=2&end_seq=3`)**:
```json
{
  "items": [
    {
      "event": "node_completed",
      "run_id": "f23e4567-e89b-12d3-a456-426614174000",
      "seq": 2,
      "ts": "2026-05-07T13:15:04.008Z",
      "source": "openjiuwen.agent.main_nodes",
      "action_id": "9f203ecb-4465-44ca-9f67-7c6a3f3021e1",
      "payload": {
        "node_name": "find_action",
        "duration_ms": 612,
        "proposals_count": 2
      }
    },
    {
      "event": "run_completed",
      "run_id": "f23e4567-e89b-12d3-a456-426614174000",
      "seq": 3,
      "ts": "2026-05-07T13:15:12.334Z",
      "source": "server.telemetry_event_server._run_search_workflow",
      "action_id": null,
      "payload": {
        "conversation_id": "53e6d4e4-65bd-49ad-9a67-a0b6138df111"
      }
    }
  ],
  "count": 2
}
```
