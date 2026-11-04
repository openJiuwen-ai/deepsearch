# DeepSearch 主 REST API

服务端 OpenAPI 为 `/api/docs`；本页给出稳定的调用契约和示例。内部 telemetry API 见 `deepsearch_rest_api.md`。

## 报告运行：`POST /api/v1/agent/deepsearch/run/`

请求体为 `DeepSearchRequest`。必填：`space_id`、`conversation_id`、`message` 和可用 `llm_config`。搜索配置由 `info_collector_search_method` 决定：`web`（默认）必须提供 `web_search_config`，`local` 必须提供 `local_search_config`，`all` 必须同时提供两者。常用可选项包括 `outliner_max_section_num`（默认 10，范围 1–15）、`workflow_human_in_the_loop`、`report_type`（`brief`/`professional`/`null`）和 `metadata`。HTTP 服务的 `outline_interaction_enabled` 默认 `false`（SDK `AgentConfig` 默认 `true`）。

联网搜索须先调用下文的 `POST /api/v1/agent/deepsearch/web_search/` 保存引擎，再将响应的 `web_search_engine_id` 作为 `web_search_config.web_search_config_id` 传入；引擎名称、密钥和 URL 由服务端按该 ID 加载。

```json
{"space_id":"space-1","conversation_id":"conv-1","message":"分析新能源市场","llm_config":{"general":{"model_name":"model","model_type":"openai","base_url":"https://example.com/v1","api_key":"***"}},"web_search_config":{"web_search_config_id":42,"max_web_search_results":5}}
```

响应是 SSE JSON 事件流。`event="waiting_user_input"` 表示 HITL 等待：保持同一 `conversation_id`，以反馈内容和 `interrupt_feedback`（`accepted`、`revise_outline` 或 `revise_comment`）再次请求。`interrupt_feedback="cancel"` 返回取消结果而非新流。参数错误返回 422；无权访问空间或资源时由服务端返回相应 4xx。

## Brief：`POST /api/v1/agent/deepsearch/run_brief/`

请求、SSE、HITL 和错误契约与 `/run/` 相同，但服务端总是将 `report_type` 设为 `brief`，忽略调用方传入的报告类型。

## 联网引擎：`/api/v1/agent/deepsearch/web_search/`

| 方法 | 路径 | 请求关键字段 | 成功响应 |
| --- | --- | --- | --- |
| POST | `/` | `space_id`、`search_engine_name`、`search_api_key`；可选 `search_url`、`extension`、`is_active` | `code`、`msg`、`web_search_engine_id` |
| GET | `/{space_id}/{web_search_engine_id}` | 路径参数 | 引擎名称、URL、扩展和启用状态 |
| GET | `/{space_id}` | 路径参数 | `data` 引擎列表 |
| PUT | `/` | `space_id`、`web_search_engine_id` 和待更新字段 | `code`、`msg`、ID |
| DELETE | `/{space_id}/{web_search_engine_id}` | 路径参数 | `code`、`msg` |
| POST | `/{space_id}/{web_search_engine_id}` | 路径参数；可选 body `query` | `datas` 搜索结果 |

## 模板：`/api/v1/agent/deepsearch/template`

导入请求为 `space_id`、`file_name`、Base64 `file_stream`、`is_template`、`template_name`、`template_desc`、`llm_config`，响应含 `code`、`msg`、`template_id`。`GET /{space_id}` 列表，`GET /{space_id}/{template_id}` 获取 Base64 内容，`PUT /` 更新（`space_id`、`template_id`、`template_content`、名称、描述），`DELETE /{space_id}/{template_id}` 删除。校验失败返回 422。

## 知识库：`/api/kb`

| 操作 | 路径与关键请求字段 |
| --- | --- |
| 创建/更新/删除 | `POST /create`、`/update`、`/delete`；均含 `space_id`，创建需 `name`、`embed_model_config`、`llm_config`；更新还必填 `kb_id`、`name`、`desc`、`embed_model_config`、`llm_config`。|
| 上传和处理 | `POST /upload`（multipart：`space_id`、`kb_id`、文件）；`/process`、`/task/progress` 跟踪索引。|
| 查询和列表 | `POST /search` 按知识库名称或描述筛选（大小写不敏感，不检索文档正文）；请求字段为 `space_id`、`query`、`page`（默认 1）和 `page_size`（默认 10，最大 100）。`/list` 用于列出知识库。|
| 文档管理 | `POST /documents/status`、`/documents/list`、`/documents/update`、`/documents/delete`。|

知识库和文档均按 `space_id` 隔离。字段以 `/api/docs` Schema 为准；SSE/HITL 与清理见 [运行流](../../../feature/server/deepsearch-run-streaming.md)。
