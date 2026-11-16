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

## 报告进度：`GET /api/v1/agent/deepsearch/run/progress`

query 参数：`space_id`（必填非空）和 `conversation_id`（必填，1–128 个 ASCII 字母、数字、下划线、连字符）。
无需请求体、模型密钥、搜索配置或模板。只查询 `search_mode=research` 下的活跃报告，包括 Brief 和采用三种编排方式的专业版报告。

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

`status` 为 `running`（执行中）、`waiting_user_input`（等待用户输入）或 `cancelling`（取消收尾中）；`node_id` 为最近进入的主图节点，
初始化时可为 `null`。章节子图不覆盖整体节点，节点允许重复或回跳；不返回完成比例或预计时间。
`updated_at` 表示节点或状态更新的本地时间，与执行进程日志使用同一时区；采用带时区偏移量的 ISO 8601 格式，不是心跳时间。
专业版 `parallel` 使用 `editor_team`，`dependency_driving` 使用 `dependency_editor_team`，
`hybrid` 根据路由选择；Brief 使用 `brief_*` 专用节点。
完整的[各模式 node_id 清单](../../../feature/framework/report-progress.md#node_id)见特性文档；清单不是固定执行顺序，调用方应兼容新增节点。

新增汇总字段含义：

| 字段 | 含义 |
| --- | --- |
| `collected_info_count` | 已有采集记录中的检索结果条目数，不保证跨查询或跨章节去重 |
| `total_sections` | 当前大纲章节总数，尚未确定大纲时为 `null` |
| `completed_sections` | 正常结束并产出非空正文的章节数，失败退出或空结果不计入 |
| `processing_sections` | 最多五个处理中的章节，只含 `section_id` 和 `title`；SDK 为不可变元组，HTTP 为数组 |

资料数复用既有记录：专业版按各章节、计划、步骤的查询结果条目数汇总；Brief 使用现有
`collection_context.search_results` 的数量，不计用户素材、引用登记数或全文读取数。
同一业务记录更新时替换原数量，不因重复通知或内部重试额外累加；不同查询或章节的相同资料可能重复计入。
交互继续时，从主图已有大纲、章节计划、报告和 Brief 状态恢复，支持换 worker；不保存第二套累计历史。
大纲版本变化时移除旧版本观察数据，章节进展以当前版本为准。
依赖模式研究结束不计为章节完成，需等待写作结果；Brief 按单章实际完成更新。
章节标识最多 128 字符、标题最多 160 字符，处理中章节只展示大纲顺序的前五个，不代表全部并发章节。
仅汇总变化不会修改 `updated_at`，该字段继续表示主图节点或状态变化时间。

| HTTP 状态 | 含义 |
| --- | --- |
| 200 | 返回状态、检索结果条目数和章节进度 |
| 404 | Redis 无有效共享记录，或非 Redis 本进程无对应活跃任务 |
| 422 | 缺少必填参数或参数非法 |
| 503 | Redis 无法读取或共享记录损坏 |

等待用户输入及等待期间断开 SSE 后仍可查询。报告最终结束后移除记录，不保留历史结果。
Redis 部署支持任意 worker 查询，首次发布前可能短暂 404；变化最多每秒合并发布一次，未变化时默认每 5 秒只续期，记录有效期为 30 秒。
非 Redis 部署需将查询发往执行进程。发布故障不阻止报告运行，过期记录不由旧发布任务重建。
Redis 部署在开始执行前最多等待 2 秒准备进度轮次；若无法确认分配结果，报告继续执行，SDK 本地进度仍可用，但本轮不再共享发布。已确认轮次后的发布失败仍会重试；迟到的旧首次发布不能覆盖新轮次或复活已移除的快照。
查询只读取进度，不执行报告或改变任务状态，也不会为共享记录续期。
SDK 查询见 [报告进度查询](research_progress.md)，设计边界见 [特性说明](../../../feature/framework/report-progress.md)。

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
