## Telemetry 后端 API

Telemetry 后端由 `server.telemetry_event_server` 提供（FastAPI，默认 `http://127.0.0.1:8089`）。能力包括：事件上报、后台 DeepSearch 运行、运行取消、JSONL 事件查询。

### 启动命令

在项目根目录执行：

```bash
uv run python -m server.telemetry_event_server
```

如果使用通配绑定地址（例如 `--host 0.0.0.0`），服务端现在会默认使用 loopback 作为内部 telemetry 回调基址。你仍可通过 `--public-base-url` 显式覆盖。

### `GET /health`
轻量存活探针（返回纯文本）。

**响应**：
- `200 OK`，响应体为纯文本：
  - 启用文件落盘时：`ok; append JSONL to <path>`
  - 使用 `--no-jsonl` 时：`ok (no JSONL file)`

**示例输出**：
```text
ok; append JSONL to /Users/dev/deepsearch/output/telemetry_logs/telemetry.jsonl
```

### `GET /`
根路径健康检查，与 `GET /health` 行为一致。

**响应**：
- `200 OK`，纯文本。

**示例输出**：
```text
ok; append JSONL to /Users/dev/deepsearch/output/telemetry_logs/telemetry.jsonl
```

### `POST /events`
Telemetry 事件写入接口（路径可由 `--path` 配置，默认 `/events`）。

接收单个 JSON 对象；若开启 JSONL 落盘，则按一行一条写入日志文件。

**请求体**：
- JSON 对象（常见字段：`event`、`run_id`、`seq`、`ts`、`payload`）。
- 空请求体也允许，会按 `{}` 处理。

**响应**：
- `204 No Content`：成功。
- `400 Bad Request`：请求体不是合法 JSON，或不是 JSON 对象。

**示例请求体（规范事件 JSON）**：
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

**示例输出（成功）**：
```text
HTTP/1.1 204 No Content
```

**示例输出（无效请求）**：
```json
{
  "detail": "Bad Request"
}
```

### `POST /runs`
启动后台 DeepSearch 图运行（支持 `search` / `react`，不支持 `research`），内部调用 `main.run_jiuwen_workflow`。

**请求体**（`CreateSearchRunRequest`）：
- `query`（`str`，必填）：用户问题。
- `llm`（`object`，必填）：必须包含 `model_name`、`base_url`、`api_key`。
- `search_mode`（`"search" | "react"`，默认 `"search"`）：
  - `search`：DeepSearch 多步图工作流。
  - `react`：简单 ReAct 循环（单 LLM + 工具调用），工具族与 search 一致。
- `enable_question_router`（`bool`，默认取自 `Config().agent_config`）：当其为 `true` 且 `search_mode="search"` 时，会先经 LLM 路由将简单问题切到 `react`（0→react，1→DeepSearch），因为这类问题使用完整 DeepSearch 流水线会过重。
- `run_id`（`str | null`，可选）：不传则服务端生成 UUID。
- `conversation_id`（`str | null`，可选）：不传则服务端生成 UUID（用于 API 生命周期关联）。
- `tool_map`（`"search_fetch" | "retrieve"`，默认取自 `PerQuestionParams`）。
- `web_fetch_provider_config` 和 `web_search_engine_config`（`tool_map="search_fetch"` 时必须同时提供）。其中 `web_fetch_provider_config.provider_name` 必须显式设置，v1 支持 `jina`。
- `milvus`（`object`，可选）：Milvus/Embedding 配置；当 `tool_map="retrieve"` 时要求 embedder key/base URL。
- `search_workflow_per_question_params`（`object`，可选）：浅覆盖参数，会按 `PerQuestionParams` 校验。

**响应**：
- `201 Created`，JSON：
  - `run_id`：实际运行 ID
  - `status`：`"started"`
  - `conversation_id`：API 生命周期会话 ID

**错误**：
- `409 Conflict`：`run_id` 已在运行中。
- `422 Unprocessable Entity`：参数校验失败（如缺少可解析的 search/fetch 配置、fetch provider 不受支持、覆盖字段非法）。

**Telemetry 事件“分块”含义（当前 DeepSearch telemetry JSON 事件类型）**：

| 事件类型 | 含义 |
| --- | --- |
| `run_started` | 工作流启动（包含 query 摘要、search_mode 等运行上下文）。 |
| `action_proposals_created` | `find_action` 产出候选动作（或失败并记录错误上下文）。 |
| `action_pool_snapshot` | 动作池快照已落盘（`pending` / `running` / `completed`）。 |
| `state_created` | 生成了一个或多个新状态（如初始状态或 action patch）。 |
| `messages_updated` | 消息历史快照已更新（LLM 回合/工具回合/最终阶段）。 |
| `action_result_saved` | 单个 action 执行结果已写入结果文件。 |
| `search_final_result` | 最终 `SearchFinalResult` 已生成并完成收尾。 |

**示例请求体**：
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

**示例输出（201）**：
```json
{
  "run_id": "f23e4567-e89b-12d3-a456-426614174000",
  "status": "started",
  "conversation_id": "53e6d4e4-65bd-49ad-9a67-a0b6138df111"
}
```

**示例输出（409）**：
```json
{
  "detail": "run_id already in progress"
}
```

### `POST /runs/{run_id}/cancel`
取消正在运行的任务。

**路径参数**：
- `run_id`（`str`）：待取消任务 ID。

**响应**：
- `204 No Content`：已接受取消请求。
- `404 Not Found`：任务不存在或已结束。

**示例输出（204）**：
```text
HTTP/1.1 204 No Content
```

**示例输出（404）**：
```json
{
  "detail": "unknown or finished run_id"
}
```

### `GET /telemetry/recent`
读取最近 N 条 telemetry 事件（可按 `run_id` 过滤）。

**查询参数**：
- `n`（`int`，必填）：返回条数，服务端会约束到 `[1, 10000]`。
- `run_id`（`str`，可选）：仅返回指定运行 ID 的事件。

**响应**：
- `200 OK`，JSON：
  - `items`：事件列表
  - `count`：返回数量

**示例输出**：
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
按 `run_id` + 序号区间（含边界）读取事件。

**查询参数**：
- `run_id`（`str`，必填）
- `start_seq`（`int`，必填）
- `end_seq`（`int`，必填，且必须 `>= start_seq`）

**响应**：
- `200 OK`，JSON：
  - `items`：匹配事件列表
  - `count`：返回数量

**错误**：
- `422 Unprocessable Entity`：`start_seq > end_seq`。

**示例输出（`run_id=f23e4567-e89b-12d3-a456-426614174000&start_seq=2&end_seq=3`）**：
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

**示例输出（`start_seq > end_seq`）**：
```json
{
  "detail": "start_seq must be <= end_seq"
}
```
