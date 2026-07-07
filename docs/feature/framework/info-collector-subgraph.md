# 信息采集子图

## 维护范围

本文档覆盖 framework 中章节资料采集的子图执行服务、采集上下文和证据账本，包括普通章节流程和依赖驱动流程复用的 collector
执行边界。

不覆盖资料筛选、打分和总结算法的 Prompt 细节；算法行为见 [资料采集](../algorithm/research-collector.md)。

## 功能目的

信息采集子图把章节 plan 中的检索步骤转换为可执行的 ReAct 采集过程，统一管理查询生成、web/local/runtime API 工具调用、证据记录、
循环上限和结构化结果回填。

## 可见行为

- 章节 `InfoCollectorNode` 会为当前 plan 调用 `CollectorExecutionService.run_plan`。
- 每个 step 会生成检索 query，并根据配置使用 web、本地或 runtime API 工具。
- supervisor 会在每轮采集后判断证据是否充分；证据不足但继续检索预计没有信息增益时，会提前进入 summary。
- 采集结果会写回 step 的 `retrieval_queries`、`doc_infos`、`step_result` 和 `evaluation`。
- `info_collector_webpage_enrich_enable=True` 时，`InfoRetrievalNode` 和 `SupervisorNode` 之间会启用网页正文增强节点。
- 没有采集到文档时，调用方会记录 `INFO_COLLECTING_EMPTY` warning。
- 依赖驱动模式可为满足依赖的多个 step 并行启动独立 collector workflow session。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/collector_context.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/collector_execution_service.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/evidence_ledger.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/graph_builder.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/info_collector.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/webpage_enrichment.py`
- `openjiuwen_deepsearch/algorithm/research_collector/webpage_enrichment.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/editor_team_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_reasoning_team_nodes.py`
- `tests/framework/test_background_knowledge.py`
- `tests/info_collector/test_webpage_enrichment.py`
- `tests/info_collector/algorithm/test_tool_log.py`

## 核心流程

1. 上游章节节点把当前 plan、step、语言、章节索引和采集上限封装为 collector 输入。
2. collector 子图初始化 `CollectorContext` 和证据账本。
3. 采集节点准备可用工具，执行 LLM 决策和工具调用循环。
4. 工具返回的搜索结果合并进 source store，并更新 `retrieval_queries.doc_infos`。
5. 如果启用网页正文增强，节点从本轮新增 `doc_infos` 中选择少量高价值 HTTP/HTTPS URL，并行抓取单页正文。
6. 网页正文增强节点把旧证据和抓取正文交给 LLM 合并为 bounded evidence；仅在质量门禁通过后刷新当前文档、累计文档和历史 query 中的同一证据。
7. supervisor 根据 evidence ledger、当前资料和缺口决定是否继续；`is_sufficient=true`、达到循环上限、
   `should_continue=false` 或没有后续 query 时，进入 summary。
8. 达到停止条件或异常边界后，输出结构化 `doc_infos`、`info_summary`、`evaluation` 和消息历史。
9. 上游章节节点把结果回填到 plan step，并累计章节已采集文档数。

## 网页正文增强

网页正文增强是 DeepResearch 信息采集子图的可选节点，默认关闭。开启后，节点位于 `InfoRetrievalNode` 和 `SupervisorNode` 之间，用于让 LLM 判断搜索引擎返回的 URL 是否值得进一步抓取完整网页正文。

配置入口：

- `AgentConfig.info_collector_webpage_enrich_enable`：默认 `False`。只有开启时才执行该节点。
- `ServiceConfig.info_collector_webpage_enrich_max_urls`：默认 `3`。每轮最多增强的 URL 数。
- `ServiceConfig.info_collector_webpage_enrich_fetch_timeout_seconds`：默认 `45`。单个 URL 抓取超时时间。

运行流程：

1. 从本轮新增 `collector_context.new_doc_infos_current_loop` 构造候选列表。
2. 过滤非 HTTP/HTTPS URL、重复 URL、已增强条目；URL 去重统一复用 collector 的 canonical URL 规则，移除常见跟踪参数和 fragment、规范化 scheme/host，但保留可能区分资源的路径大小写。候选最多保留 10 个，并按已有 `scores` 做轻量排序。
3. 候选交给选择 LLM，候选字段包含 `candidate_index/title/url/source/query/key_passages/scores`，不包含 `doc_index` 和 `original_content`。固定规则位于 system message，任务字段、候选字段和网页正文以不可信 JSON 放入独立 user message；模型必须忽略这些字段内的指令文本。
4. 选择 LLM 只返回 `selected_indexes`，其值必须是可见的 `candidate_index`；节点负责去重、过滤越界并限制数量。如果没有高价值网页，可以返回空列表。
5. 对选中的 URL 使用 `asyncio.gather` 并行执行 fetch 和压缩；最大并发数受 `info_collector_webpage_enrich_max_urls` 限制。
6. fetch 使用 `WebFetchWebpageAdapter.fetch_webpage_sync()`。当前能力是单页抓取，不递归爬站；遇到 `401/403/429` 时由 openJiuwen fetch 实现 fallback 到公开 Jina Reader。显式 `.pdf` URL 直接使用 Jina Reader；无扩展名 URL 的直接响应以 `%PDF-` 文件魔数开头时也切换到 Jina，避免把 PDF 对象流送入压缩 LLM。普通抓取异常，或正文少于 `max(200, 旧 original_content 长度)` 时，同样使用 Jina 重试；Jina 返回 PDF 原始数据或仍未达到动态门槛时保留旧证据。direct、PDF 和 Jina fallback 共享同一个单 URL deadline，不会在 fallback 时重新获得一份完整超时预算。当前不依赖 Jina key。
7. raw content 进入压缩 LLM 前截断到 `MAX_COLLECTOR_DOC_CONTENT_LENGTH * 10`。
8. 压缩 LLM 同时接收已有 `original_content` 和新抓取正文，合并并保留已有可验证事实；浏览器验证、CAPTCHA、访问拒绝、登录、JavaScript 提示、错误页或重定向占位页视为无效抓取内容并被忽略。输出正文保持网页来源语言，不在证据增强阶段按 collector 的 `language` 翻译；面向用户的语言本地化由后续报告生成处理。写回前限制在 `MAX_COLLECTOR_DOC_CONTENT_LENGTH` 以内。
9. 节点使用已有 `key_passages` 检查数字、单位和设备/数据集标识是否保留；匹配时忽略大小写、空格和标点差异。质量门禁通过后才集中写回 `new_doc_infos_current_loop`、累计 `doc_infos`、`history_queries[*].doc_infos` 和 `source_store`，然后交给 `SupervisorNode`、`SummaryNode` 和最终报告器使用。

增强成功后会刷新：

- `original_content`：压缩后的网页正文证据。
- `key_passages`：压缩 LLM 基于新正文生成的关键片段；为空时降级为规则抽取。
- `source_id`：基于原 `doc_id` 和新正文生成，用于区分同一文档下不同 evidence content。
- `content_ref`：指向新的 `source_store` 内容。
- `enrichment`：记录 `webpage_fetched`、抓取状态码、抓取后的 URL 和内容来源。`content_source` 为 `harness_webpage_fetch` 或 `jina_reader`；前者准确表示 openJiuwen harness 入口自身也可能执行内部 fallback。

普通抓取、Jina Reader、压缩 LLM 或质量门禁任一环节失败时，节点保留原 `original_content`、`key_passages`、`source_id`、`content_ref` 和 `source_store`，不会把失败结果标记为已增强。

增强成功后保持不变：

- `doc_id`
- `title`
- `url`
- `source`
- `query`
- `scores`
- `publish_time`

节点不会重新执行 `run_doc_evaluation()`，因此评分保持搜索结果评估阶段的原值。

算法与编排职责：

- `algorithm/research_collector/webpage_enrichment.py` 承担候选构造、Prompt user payload、选择结果清洗、证据质量门禁和多份文档同步等纯逻辑。
- `framework/openjiuwen/agent/collector_graph/webpage_enrichment.py` 只负责 session 状态、LLM/抓取调用、并发控制、日志和图节点写回。

日志：

- Info 日志记录候选数量、选中数量、最大 URL 数。
- fetch 前 Info 日志记录 URL、候选索引、doc 索引和 scores。
- fetch 成功后 Info 日志记录 `doc_id/source_id/status_code/raw_len/compressed_len/key_passages/scores`。
- 普通抓取过短、Jina Reader 失败或质量门禁拒绝替换时记录原因和长度。
- 非敏感模式下 Debug 日志只记录 `original_content` 增强前后的长度，不记录正文全文。
- 敏感模式下 fetch、质量门禁和候选异常日志只保留固定事件分类与长度，不记录 URL、异常正文、事实锚点、标题或步骤文本。

## 数据契约与依赖

- collector 输入包含 `language`、`messages`、`section_idx`、`plan_idx`、`step_idx`、`max_search_query_count`、
  `max_research_loops`、`max_tool_call_turns_per_query`、`report_type`、`research_intent`。
- collector 输出至少包含 `history_queries`、`doc_infos`、`info_summary`、`evaluation`、`messages`。
- `EvidenceLedger` 记录 accepted/rejected/pending 证据、尝试过的 query 和缺口，供后续采集轮次判断。
- `CollectorContext.should_continue` 保存 supervisor 对下一轮检索价值的判断；为 `false` 时，collector 清空后续 query
  并进入 summary。
- `max_search_query_count` 来自 `config.info_collector_max_search_query_count`，表示单轮 query 硬上限；初始 query
  生成在需要外部检索时使用 `1..max_search_query_count`，明确不需要外部检索时可返回空列表；supervisor 后续
  query 在 `0..max_search_query_count` 范围内自主决定数量。
- `max_tool_call_turns_per_query` 来自 `config.info_collector_max_tool_call_turns_per_query`，独立于
  `max_research_loops` 生效。
- 搜索工具来源于 `web_search_context`、`local_search_context` 和 runtime API 工具配置。
- 网页正文增强节点只更新匹配 doc 的正文证据和证据引用，并同步 `history_queries` 供最终报告读取；证据保持来源语言，不承担报告本地化；不修改原始 URL、标题、评分或 evaluator 结果。

## 边界与错误处理

- collector 子图使用独立 workflow session，依赖驱动并发时避免共享子图状态。
- runtime API 响应大小、JSON 深度和容器长度限制由工具层保护。
- 网页抓取正文少于 200 字符或短于旧证据时不会直接覆盖旧证据；Jina Reader 重试仍不满足动态门槛时按抓取失败处理。
- direct、PDF 和 Jina fallback 共用 `info_collector_webpage_enrich_fetch_timeout_seconds` 指定的整体 deadline；超时保留旧证据。
- PDF URL 或 PDF 原始响应必须经 Jina Reader 转换为正文；Jina 仍返回 `%PDF-` 原始数据时按抓取失败处理。
- 压缩结果丢失旧关键片段中的数字或技术标识时，质量门禁拒绝替换并保留旧证据身份；描述性内容允许同义改写或翻译。
- 空结果不会直接中断主图，但会通过 warning 进入章节和最终报告状态。
- 候选和网页内容不会插入 system prompt；其中的指令样文本只能作为不可信数据处理。
- 敏感日志模式下，采集输入、工具结果、中间消息、URL、异常正文和事实锚点不打印明文。

## 测试与验证

- `uv run pytest tests/framework/test_background_knowledge.py`
- `uv run pytest tests/info_collector/test_webpage_enrichment.py`
- `uv run pytest tests/info_collector/algorithm/test_tool_log.py`
- 网页增强测试覆盖 canonical URL 去重、Prompt 消息隔离、输出语言、整体 fetch deadline、PDF/Jina fallback、质量门禁、敏感日志脱敏、历史 query/最终报告同步和并发异常隔离。
- 修改 runtime API 工具参与采集时，补充运行 `uv run pytest tests/tools/test_runtime_api.py`。
- 修改 web/local 工具映射时，补充运行 `uv run pytest tests/tools/test_web_search.py tests/tools/search_api/`。

## 相关文档

- [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)
- [搜索工具注册与运行时 API 工具](./search-tool-registration.md)
- [资料采集](../algorithm/research-collector.md)
