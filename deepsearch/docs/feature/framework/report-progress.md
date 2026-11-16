# 报告进度查询

## 维护范围

报告主图节点与活跃状态采集、SDK 查询、HTTP 查询及 Redis 跨 worker 进度共享。
进度对象只记录和返回执行位置，不控制报告执行。

## 功能目的

报告耗时较长，调用方需要知道正在执行哪个节点、是否正在等待用户输入。
直接复用原有主图节点名称，不估算完成比例或剩余时间。
适用于 `search_mode=research` 下的 Brief 和专业版报告；专业版支持
`parallel`、`dependency_driving`、`hybrid` 三种编排方式。

## 可见行为

- `agent.get_progress(conversation_id)` 返回不可变的 `ProgressSnapshot` 或 `None`，只查询该 Agent 实例的记录。
- `GET /api/v1/agent/deepsearch/run/progress` 按 `space_id`、`conversation_id` 查询，返回状态与轻量汇总。
- SDK 开始消费 `run()` 输出后登记进度；HTTP 在创建研究报告流响应时登记，随后由 SDK 更新同一进度对象。
- 等待用户输入时仍可查询；即使断连发生在等待事件已送达、SDK 尚在异步清理资源时，也保留可恢复的进度。显式业务取消、实际失败或最终结束后移除，不保留历史结果。
- HTTP 无记录返回 404，参数错误返回 422，共享存储无法读取或记录损坏返回 503。
- Redis 部署支持任意 worker 查询共享进度；非 Redis 部署只查询当前进程。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/report_progress.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/progress_summary.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/brief_nodes.py`、`algorithm/brief_report/writer.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/base_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `server/core/report_progress_registry.py`
- `server/routers/deepsearch_run.py`、`server/schemas/deepsearch_run.py`

## 核心流程

1. 每轮创建独立进度对象，并通过运行期 ContextVar 传递到节点。
2. 节点入口核对主图标识，记录节点名称与执行进程本地更新时间（带时区偏移量）；章节子图节点不覆盖报告整体位置。
3. 观察交互事件，将状态设为 `waiting_user_input`，原样转发全部输出。
4. 节点写回业务状态后更新汇总；用户继续时创建新一轮对象，从已有大纲、计划、报告及 Brief 状态恢复数量。前一轮只清理自身记录，避免删除后续进度。
5. 本轮最终结束后移除进度记录；等待用户输入不视为最终结束。
6. Redis 部署在执行前用最多 2 秒准备共享轮次，成功后异步首次发布；随后每秒检查变化，有变化才合并发布，无变化时默认每 5 秒只续期，TTL 为 30 秒。等待用户输入时继续续期。
7. 首次发布及重试同时校验共享轮次、有效凭据与最近发布顺序；更新、续期和删除仍校验 owner。清理快照时保留短期最近发布轮次标记，防止旧首次发布覆盖或复活记录。

共享轮次准备位于 HTTP producer 的取消和结束清理范围内；准备期间收到取消时，原 SSE 仍发送取消消息并结束。
等待交互时断连会保留进度；之后若显式本地取消，即使 producer 已结束，也会关闭取消时对应轮次的进度，保留原取消事件消费方式。

## 数据契约与依赖

### 快照字段

| 字段 | 含义 |
| --- | --- |
| `conversation_id` | 报告会话标识 |
| `status` | `running` 执行中、`waiting_user_input` 等待用户输入、`cancelling` 取消收尾中 |
| `node_id` | 最近进入的主图节点；尚未进入节点时 SDK 为 `None`，HTTP 为 `null` |
| `updated_at` | 最近节点或状态变化的本地 ISO 8601 时间（带时区偏移量），不是心跳时间或健康证明 |
| `collected_info_count` | 已有采集记录中的检索结果条目数，不保证跨查询或跨章节去重 |
| `total_sections` | 当前大纲章节总数，尚未确定大纲时为 `None`（HTTP 为 `null`） |
| `completed_sections` | 正常结束并产出非空正文的章节数，失败退出或空结果不计入 |
| `processing_sections` | 最多五个处理中的章节，只含 `section_id` 和 `title`；SDK 为不可变元组，HTTP 为数组 |

`cancelling` 表示观察到已有 HTTP 取消请求后的收尾状态，收尾完成后记录移除。

`updated_at` 与执行进程业务日志使用相同的本地时区，例如北京时间返回 `2026-10-22T14:30:00+08:00`。
HTTP 查询原样返回执行 worker 生成的时间，不转换为查询 worker 的时区；部署在 UTC 时区时偏移量为 `+00:00`。

### 轻量汇总口径

资料数复用既有记录：专业版按各章节、计划、步骤的查询结果条目数汇总；Brief 使用现有
`collection_context.search_results` 的数量，不计用户素材、引用登记数或全文读取数。
同一业务记录更新时替换原数量，不因重复通知或内部重试额外累加；不同查询或章节的相同资料可能重复计入。
依赖采集输入中的计划和步骤编号保持原样；进度观察通过任务上下文传递完整业务标识，
确保实时采集与主图回写按同一记录替换，且不混淆并行步骤或其他章节。
交互继续时，从主图已有大纲、章节计划、报告和 Brief 状态恢复，支持换 worker；不保存第二套累计历史。
大纲版本变化时移除旧版本观察数据，章节进展以当前版本为准。
依赖模式研究结束不计为章节完成，需等待写作结果；Brief 按单章实际完成更新。
章节标识最多 128 字符、标题最多 160 字符，处理中章节只展示大纲顺序的前五个，不代表全部并发章节。
仅汇总变化不会修改 `updated_at`，该字段继续表示主图节点或状态变化时间。

查询与 Redis 发布只读取汇总，不遍历完整资料历史。数量按业务记录身份替换，不保存正文或额外的 checkpoint 字段。
HTTP 快照通过列表和文本上限控制体积，使用 UTF-8 JSON，当前合法字段范围内小于 8 KiB。
兼容读取旧的四字段共享记录，缺少的扩展字段使用初始值。

### node_id

下表集中列出当前各报告模式可能返回的节点。✓ 表示该模式可能返回，— 表示不属于该模式。
Brief 对应 `report_type=brief`；其余三列对应专业版的 `execution_method`。
三个研究主图都注册了 Brief 分支，实际返回哪些节点由报告类型和运行时路由决定。

| node_id | 含义 | Brief | parallel | dependency_driving | hybrid |
| --- | --- | --- | --- | --- | --- |
| `start` | 初始化上下文 | ✓ | ✓ | ✓ | ✓ |
| `intent_recognition` | 识别意图与报告类型；hybrid 同时选择编排方式 | ✓ | ✓ | ✓ | ✓ |
| `generate_questions` | 生成澄清问题 | ✓ | ✓ | ✓ | ✓ |
| `feedback_handler` | 等待并处理澄清回答 | ✓ | ✓ | ✓ | ✓ |
| `brief_outline` | 生成 Brief 大纲 | ✓ | — | — | — |
| `brief_info_collector` | 收集或补充 Brief 资料 | ✓ | — | — | — |
| `brief_evidence_reviewer` | 审核证据并判断是否补充资料 | ✓ | — | — | — |
| `brief_sub_reporter` | 生成 Brief 内容单元 | ✓ | — | — | — |
| `brief_reporter` | 汇总 Brief 报告 | ✓ | — | — | — |
| `brief_source_tracer` | 处理 Brief 引用溯源 | ✓ | — | — | — |
| `brief_html_reporter` | 生成 Brief HTML | ✓ | — | — | — |
| `outline` | 生成专业版大纲 | — | ✓ | ✓ | ✓ |
| `outline_interaction` | 等待并处理大纲反馈 | — | ✓ | ✓ | ✓ |
| `editor_team` | 并行研究与章节写作 | — | ✓ | — | ✓ |
| `dependency_editor_team` | 按章节依赖研究与写作 | — | — | ✓ | ✓ |
| `reporter` | 汇总专业版报告 | — | ✓ | ✓ | ✓ |
| `vlm_chart_generator` | 处理图表生成 | — | ✓ | ✓ | ✓ |
| `source_tracer` | 处理引用溯源 | — | ✓ | ✓ | ✓ |
| `source_tracer_infer` | 处理推理链溯源 | — | ✓ | ✓ | ✓ |
| `user_feedback_processor` | 处理报告反馈 | — | ✓ | ✓ | ✓ |
| `end` | 汇总本轮结果并结束 | ✓ | ✓ | ✓ | ✓ |

清单表示可能值，不是固定执行顺序，也不要求每次经过所有节点。节点可能重复或回跳，
`start`、`end` 等短暂节点可能被轮询漏过；报告最终结束后不会保留 `end` 快照。
`hybrid` 根据路由选择团队节点，一次报告不一定经过两个团队。
依赖编排的大纲节点仍返回 `outline`、`outline_interaction`。

`brief_sub_reporter` 是主图节点，可以返回；章节子图中的 `sub_reporter`、`plan_reasoning`、
`info_collector` 等不返回。`framework` 是消息标识，`evaluator`、`human_evaluator` 未注册到当前研究主图，均不在此清单中。
清单以当前工作流注册为准；调用方应兼容未来新增节点，例如对未知名称直接显示原值。

### HTTP 参数与共享存储

HTTP query 参数为非空 `space_id` 和合法 `conversation_id`（1–128 个 ASCII 字母、数字、下划线、连字符）。
查询无需模型凭据。Redis 复用服务的 `checkpointer_type=redis`、`redis_url`、`redis_cluster_mode` 配置。
进度记录使用独立 key `deepsearch:progress:{空间与会话摘要}:active`，Hash 仅包含 `owner` 和 `snapshot`。
`owner` 区分进度发布轮次，不决定执行权限；`snapshot` 保存上述进度字段。
进度记录的有效期独立于报告执行数据的保存期限。

另有同一 hash tag 下的短期元数据，不包含报告正文：

| key 后缀 | 用途 |
| --- | --- |
| `:sequence` | 当前序列的 `epoch` 和递增 `version`，执行前分配；过期后换新 epoch，避免旧版本值重用 |
| `:round:<token>` | 本轮有效发布凭据，过期或被清理后，旧请求不能重新登记 |
| `:latest` | 最近成功发布的 `epoch:version`，拒绝更旧的首次发布及其重试 |

分配只在执行前尝试一次，不在执行后的重试中重新取号；最多等待 2 秒，失败或响应不确定时继续业务执行，
本轮停止共享发布，SDK 本地观察仍可用。已经确认轮次后的首次发布失败可使用同一凭据重试。
发布和续期原子延长这些元数据的有效期；结束时删除快照和本轮凭据，最近发布轮次标记和序列等待 TTL 清理。
旧请求即使在新快照删除、全部元数据过期或序列重建后到达，也不能重建旧记录。

## 边界与错误处理

- SDK 查询只访问当前 Agent 的内存记录；新建另一个 Agent 无法查询已有实例的任务。
- `run()` 的参数、输出内容与顺序保持不变，进度采集不改变原有执行流程。
- 进度数据不是执行锁，不改变同会话启动或继续执行的规则。
- Redis 发布是异步操作，首次发布前可能返回 404，后续查询通常有约一个发布周期的延迟。
- 发布故障只影响进度可见性，不阻止报告执行。共享记录过期或被后续轮次覆盖后，旧发布任务不重建它；SDK 本地观察仍可继续，新一轮执行会登记新记录。
- 查询不会续期。共享记录尚未过期不证明任务正常运行，404 仅表示当前没有可返回的进度记录。

## 测试与验证

在 `deepsearch/` 目录执行：

```bash
uv run pytest tests/workflow/test_report_progress.py tests/workflow/test_progress_summary.py tests/workflow/test_workflow_run.py
uv run pytest tests/brief_report/test_writer.py
uv run pytest tests/server/test_report_progress_api.py tests/server/test_report_progress_registry.py
uv run pytest tests/workflow/test_dependency_editor_team_node.py tests/server/test_deepsearch_run.py
```

自动化测试覆盖图执行、失败、等待与继续、主图节点过滤、参数校验、输出透传、前后轮次隔离、
共享查询、TTL、发布故障和共享记录损坏，不调用真实模型。
另覆盖记录数量替换、跨实例交互恢复、大纲版本变化、Brief 单章进度、依赖研究与写作区分、不可变和有界快照，以及无变化时仅续期。
生命周期回归覆盖等待事件送达后、异步资源关闭期间断连及显式业务取消。真实 Redis 回归覆盖首次发布乱序、响应丢失重试、新快照删除或过期、序列重建和轮次分配故障。
轮次准备期间的回归通过真实 ASGI SSE 响应验证本地取消、远程取消与断连都能结束连接并停止发布；分配失败或超时仍继续报告执行。
本地取消回归分别使用内存与 SQLite checkpoint，覆盖等待断连后取消、重复取消和后续取消事件消费，以及异步清理期间新轮次的进度保护。
依赖汇总回归执行真实采集子图、依赖步骤调度和计划回写，覆盖多计划、多并行步骤、其他章节数据保留及交互恢复。
共享测试启动隔离 Redis，不连接业务 Redis；需要本地 `redis-server` 或设置 `DEEPSEARCH_TEST_REDIS_SERVER` 路径。
跨 worker 查询测试使用同一进程中的隔离路由和注册器配合真实 Redis；不等同于真实双进程、真实模型的实机验收。

## 相关文档

- [SDK 报告进度查询](../../zh/3.开发指南/API文档/research_progress.md)
- [HTTP API](../../zh/3.开发指南/API文档/server_rest_api.md)
