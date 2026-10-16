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
- 存在时间范围时，初始 query 与 supervisor 补搜 query 由 LLM 自然表达该范围；每条 query 最多 5 个主题关键词，
  时间短语不计入该限制。`source_date` 表示资料发表时间，`content_date` 表示事实或数据时间。
- 时间约束采用召回优先的 best-effort：只删除 Tavily 返回的可确认发表日期且明确越界的文档，日期未知、其他 web 引擎和 local 文档继续进入证据。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/collector_context.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/collector_execution_service.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/evidence_ledger.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/graph_builder.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/info_collector.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/webpage_enrichment.py`
- `openjiuwen_deepsearch/algorithm/research_collector/webpage_enrichment.py`
- `openjiuwen_deepsearch/algorithm/research_collector/enrichment_quality.py`（压缩 Prompt 的事实表达提取）
- `openjiuwen_deepsearch/algorithm/research_collector/enrichment_content.py`（无效网页检查与正文清理）
- `openjiuwen_deepsearch/framework/openjiuwen/tools/fetch_api/webpage_cache.py`（正文缓存与并发抓取合并）
- `openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`（复用已有研究约束）
- `openjiuwen_deepsearch/utils/constants_utils/session_contextvars.py`（运行时缓存上下文）
- `openjiuwen_deepsearch/algorithm/prompts/collector_webpage_enrichment_select/`
- `openjiuwen_deepsearch/algorithm/prompts/collector_webpage_enrichment_compress/`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/editor_team_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`（首次章节执行的缓存作用域与异常收尾）
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_reasoning_team_nodes.py`
- `tests/framework/test_background_knowledge.py`
- `tests/info_collector/test_webpage_enrichment.py`
- `tests/info_collector/algorithm/test_tool_log.py`

## 核心流程

1. 上游章节节点把当前 plan、step、语言、章节索引和采集上限封装为 collector 输入。
2. collector 子图初始化 `CollectorContext` 和证据账本。
3. 采集节点准备可用工具，执行 LLM 决策和工具调用循环。
4. Tavily 返回的 web 结果先归一化日期元数据；`source_date` 明确越界的文档在 evaluator 和 evidence 构建前整篇删除，
   范围内或日期未知的文档保留。`content_date` 和 local 结果不执行来源日期过滤。
5. 如果启用网页正文增强，节点从本轮新增 `doc_infos` 中选择少量高价值 HTTP/HTTPS URL，并行抓取单页正文。
6. 网页正文增强节点把旧证据和抓取正文交给 LLM 合并为 bounded evidence；压缩成功且正文非空后直接刷新当前文档、累计文档和历史 query 中的同一证据。
7. supervisor 根据 evidence ledger、当前资料和缺口决定是否继续；`is_sufficient=true`、达到循环上限、
   `should_continue=false` 或没有后续 query 时，进入 summary。
8. 达到停止条件或异常边界后，输出结构化 `doc_infos`、`info_summary`、`evaluation` 和消息历史。
9. 上游章节节点把结果回填到 plan step，并累计章节已采集文档数。

## 网页正文增强

网页正文增强是 DeepResearch 信息采集子图的可选节点，默认关闭。开启后，节点位于 `InfoRetrievalNode` 和 `SupervisorNode` 之间，用于让 LLM 判断搜索引擎返回的 URL 是否值得进一步抓取完整网页正文。

配置入口：

- `AgentConfig.info_collector_webpage_enrich_enable`：默认 `False`。只有开启时才执行该节点。
- `ServiceConfig.info_collector_webpage_enrich_max_urls`：默认 `5`。每轮最多增强的 URL 数。
- `ServiceConfig.info_collector_webpage_enrich_fetch_timeout_seconds`：默认 `45`。单个 URL 抓取超时时间。
- `AgentConfig.web_fetch_provider_config`：可选。Jina Reader 路据此解析 provider（`provider_name` / `api_key` / `base_url`），契约见 [web fetch provider 注册](./web-fetch-provider-registry.md)。

运行流程：

1. 从本轮新增 `collector_context.new_doc_infos_current_loop` 构造候选列表。
2. 过滤非 HTTP/HTTPS URL、重复 URL、已增强条目；URL 去重统一复用 collector 的 canonical URL 规则，移除常见跟踪参数和 fragment、规范化 scheme/host，但保留可能区分资源的路径大小写。候选最多保留 10 个，并按已有 `scores` 做轻量排序。
3. 候选交给选择 LLM，候选字段包含 `candidate_index/title/url/source/query/key_passages/publish_time`，不包含 `doc_index` 和 `original_content`。`publish_time` 仅转发已有 `doc_time/publish_time`，空值保持未知，不从URL推断。固定规则位于 system message，任务字段、候选字段和网页正文以不可信 JSON 放入独立 user message；模型必须忽略这些字段内的指令文本。
4. 选择 LLM 只返回 `selected_indexes`，其值必须是可见的 `candidate_index`；节点负责去重、过滤越界并限制数量。如果没有高价值网页，可以返回空列表。
5. 对选中的 URL 使用 `asyncio.gather` 并行执行 fetch 和压缩；每轮 URL 数由 `info_collector_webpage_enrich_max_urls` 限制，默认 `5`。单轮最多同时执行 `ENRICH_FETCH_CONCURRENCY=5` 个增强任务，并发槽覆盖抓取与压缩，缓存命中也占用槽位；该限制不跨章节共享。
6. 首次章节执行范围内默认共享正文缓存，以 URL 原字符串精确查询。缓存满足本次 `max(200, 旧 original_content 长度)` 门槛时直接复用；正文不足或未命中时进入原抓取流程。同 URL 的在途抓取共享一次执行，各调用者有独立 deadline，等待耗时计入预算；共享正文低于当前调用者门槛时使用剩余预算补抓。缺少缓存上下文时执行原流程。缓存命中仍占每轮增强名额。
   每个 URL 依次尝试三条抓取路，前一条拿不到可用正文才退到下一条；三路共享同一个单 URL deadline（见 `info_collector_webpage_enrich_fetch_timeout_seconds`，默认 45 秒），退到下一条时不会重新获得完整超时预算。当前能力是单页抓取，不递归爬站。
   - **harness 直连**（`WebFetchWebpageAdapter.fetch_webpage_sync()`）：以完整 Chrome UA 直连目标站。该入口在目标站返回 `401/403/429` 时，会由 openJiuwen fetch 实现内部 fallback 到第三方包内置的匿名 Jina Reader（地址与请求头写死、不可配置），因此该路返回值不代表一定来自直连。
   - **httpx 直连**（`_fetch_via_simple_ua`）：换请求客户端以覆盖连接层被拦的站点；用 pypdfium2 把响应 PDF 字节**本地解析**为正文（最多 `SIMPLE_UA_PDF_MAX_PAGES`，默认 50 页），补直连路缺失的 PDF 能力。
   - **Jina Reader 代理**（`JinaWebFetchProvider.fetch_page`）：provider 由 `_pre_handle` 经 `resolve_web_fetch_provider` 从 `agent_config.web_fetch_provider_config` 解析，未配置时默认构造；并发向多个 reader base（默认 `r.jinaai.cn` 与 `r.jina.ai`）竞速取先返回者，单个 base 鉴权失败不再短路整次竞速。
   判定与回退：URL 以 `.pdf` 结尾时跳过 harness 直连、直接进入 httpx 直连；正文少于 `max(200, 旧 original_content 长度)` 时继续退下一路；任一路返回 `%PDF-` 原始数据或三路都未达到动态门槛时保留旧证据。
7. raw content 进入压缩 LLM 前截断到 `MAX_COLLECTOR_DOC_CONTENT_LENGTH * 10`。
8. 压缩 LLM 同时接收已有 `original_content`、新抓取正文与 `required_facts`（从旧关键片段提取、供模型参考的原始事实表达），合并并保留已有可验证事实；表达必须与实体、单位和条件关联，不应机械附加一串数字。浏览器验证、CAPTCHA、访问拒绝、登录、JavaScript 提示、错误页或重定向占位页视为无效抓取内容并被忽略。输出正文保持网页来源语言，不在证据增强阶段按 collector 的 `language` 翻译；面向用户的语言本地化由后续报告生成处理。`target_content_length` 为硬上限减500字符的软目标；必要时可使用到硬上限，`max_content_length` 仅限制 `original_content` 字符数，不限制整个 JSON 或 `key_passages` 的合计长度。程序仍执行最终长度限制，不增加二次压缩调用。
9. 抓取和结构化压缩成功、最终正文非空后，直接替换旧 `original_content`，不再执行旧事实或锚点保留门禁。集中写回 `new_doc_infos_current_loop`、累计 `doc_infos`、`history_queries[*].doc_infos` 和 `source_store`，然后交给 `SupervisorNode`、`SummaryNode` 和最终报告器使用。



选择与压缩共用上游 `collector_context.research_intent` 的紧凑 `research_constraints`：必需维度、比较对象、分别表达的来源日期/事实日期指令；仅在既有 `exclusion_constraint_enable` 开启时加入原禁引指令。兼容旧单时间字段，转换不修改运行状态，不新调用模型解析要求。无约束时不凭空添加，结构无效时回退已有任务描述并只记固定告警。压缩约束引导新增证据，不删除或改写必须保留的旧事实来伪造日期合规。Prompt 要求保持同一试验的实体/方法/浓度/温度与同一样本的分母，不根据缩写猜机制；当前题目的答案或评分材料不得当研究主题的事实依据。这些是已有模型调用内的行为约束，不是新增确定性过滤器或完整事实验证。

增强成功后会刷新：

- `original_content`：压缩后的网页正文证据。
- `key_passages`：压缩 LLM 基于新正文生成的关键片段；为空或模型正文发生截断时，从最终保留正文重新规则抽取，避免片段引用已切掉的尾部。
- `source_id`：基于原 `doc_id` 和新正文生成，用于区分同一文档下不同 evidence content。
- `content_ref`：指向新的 `source_store` 内容。
- `enrichment`：记录 `webpage_fetched`、抓取状态码、抓取后的 URL 和内容来源。`content_source` 取 `harness_webpage_fetch`（harness 直连路；注意该入口自身也可能执行内部 fallback）、`simple_ua`（httpx 直连路）或 `jina_provider`（Jina Reader 路）。
- `enrichment.content_changed`：标记最终正文是否与旧正文不同（忽略首尾空白）。相同正文仍记录 `webpage_fetched=True`，避免该条目后续再次增强；只记录 `enrichment_unchanged`，不计入正文更新成功事件。不同正文不等同于已经验证新增事实。



三路抓取失败、压缩调用失败、结构无效或压缩正文为空时，节点保留原 `original_content`、`key_passages`、`source_id`、`content_ref` 和 `source_store`，不会把失败结果标记为已增强。


从 1 开始的连续列表序号不作为测量值提取，孤立编号、日期和正文数值继续保护。全大写缩写末尾的小写 s 仅在旧文同时明确使用单数缩写时可视为英语复数（如 VLSA/VLSAs），不随意缩短未知标识或合并不同设备名。

普通复合描述词（例如 low-voltage）与数字/缩写技术标识分开提取。这些描述条件及 F-tests、HYSA-QZ 等技术标识作为 Prompt 提示，不执行压缩后的表达匹配。压缩载荷的 `required_descriptions` 用原关键片段索引关联其主语和事实，不重复添加上下文正文。Prompt 在同一次调用内要求逐片段自检旧定性结论、功耗/面积、限制和机理，先精简重复内容及新增措辞。代码侧不验证定性语义等价，也不执行事实缺失拦截。

压缩 Prompt 在原一次模型调用内使用研究维度作为覆盖清单，优先提取主文的机制、元件与连接、控制时序、带条件的定量结果和代价，再删除重复措辞；未知项不猜测，不设置最低字数、不增加二次扩写。旧事实清单用于 Prompt 提示，不作为写回拒绝条件。

推荐区分离后主文为空时按 `no_primary_content` 拒绝该抓取结果，不缓存、不调用压缩模型，继续原抓取回退。主文视图只是结构提取结果，不保证平台 AI 摘要或 FAQ 已被排除，也不认证其为作者原文。

正文通过现有抓取检查后即缓存，压缩失败不删除正文；不同步骤仍独立执行压缩。失败、空结果、未解析 PDF 不缓存，后续独立请求可重试。正文不足补抓失败时保留旧缓存。调用者取得独立副本，修改结果不会污染共享正文。

事实提示清单在每次压缩载荷构造中只提取一次，数值提示与描述条件关联共用该结果；描述关联所需的旧片段也只清洗一次，并保留原片段索引与空片段 fallback 行为。

已清理正文使用内部字典类型携带正文、标题及分区字段快照；内容未变时，后续准备与压缩入口复用清理结果，避免重复扫描脚本及推荐区。缓存存储前校验有效性、长度与 PDF，命中及共享结果返回时只比较本次长度门槛。外部普通字典即使包含同名标记也须完整清理；返回副本的正文、标题或分区字段改变后重新清理，不沿用旧结果。

每条抓取路接受结果前先检查正文有效性：明确的验证/登录/错误标题同时出现对应占位提示时拒绝，删除 script/style 标签包裹的非正文内容，再执行原长度门槛。普通文章讨论 CAPTCHA 不按关键词拒绝。无效结果沿用剩余 deadline 继续原抓取回退，不缓存、不进入压缩；三路都失败时保留旧证据。缓存和直接压缩入口也执行相同有效性检查。该规则保守判断明确占位页，不保证识别所有登录墙或裸脚本。

主文分区采用与研究步骤无关的结构规则：HTML/Markdown 中明确的推荐标题下面至少出现两个不同 URL 的独立论文卡片时，排除该标题所在区段，遇到同级或更高层标题恢复保留正文。有明确匹配的主文章一级标题时，同时去掉标题前的导航。正文 `Related Work`、普通 References 和缺少卡片结构的模糊标题不会被直接截断。分区结果作为 `primary_content` 随原文一起缓存在内存中，压缩载荷只取主文视图；无明确结构时保留原文并由原压缩调用保守判断。旧证据不自动删除，Prompt 要求区分旧片段与当前主文的来源，不能将推荐论文的事实、作者或日期归给当前文章。规则不保证修复所有历史来源污染。

缓存由首次章节执行节点 `EditorTeamNode.invoke` 持有，依赖编辑团队继承同一入口，混合模式沿用其选中的编辑团队。每次节点调用独立创建缓存，经运行时 `ContextVar` 供该次调用的章节、步骤和轮次共享，不进入 workflow 输入或持久化状态。作用域包含原编辑团队的章节子图；退出编辑团队后即关闭，不延续到全局报告或报告后用户反馈。前置需求澄清、大纲确认以及后置反馈补搜均不创建或保留该缓存。

执行范围内无 TTL、无容量上限。正常返回、异常或取消均在 `finally` 中关闭缓存并恢复外层上下文；并发章节发生异常时先取消并等待其他在途章节，再释放正文池。每次新执行使用新缓存，不需要会话缓存表、HITL暂停判断或服务端会话驱逐挂钩。单个等待者取消不取消共享抓取；执行范围退出时取消在途抓取协程，底层线程继续受原请求预算约束，迟到结果不得写回。缺少缓存上下文的直接collector调用走原独立抓取流程，独立 `web_fetch` 工具也不接入该缓存。

普通章节、依赖层任务以及依赖编辑团队最后一层纯写作统一通过 `_gather_section_tasks` 汇总；任一任务异常或父任务取消时，取消并等待同批其他任务后再传播异常。最后一层写作本身不执行网页增强，该汇总只统一任务收尾，不修改写作逻辑。

共享抓取沿用发起者的截止时间与最低正文长度，其他等待者不会延长其抓取预算；各等待者自己的等待截止时间独立。共享抓取失败时，同一批等待者直接返回失败，不立即循环重试；后续独立请求可以重试。共享抓取成功但不满足某个等待者的长度门槛时，该等待者使用自己的剩余预算补抓。

增强成功后保持不变：

- `doc_id`
- `title`
- `url`
- `source`
- `query`
- `scores`
- `publish_time`

节点不会重新执行评分，因此评分保持搜索结果评估阶段的原值。

算法与编排职责：

- `algorithm/research_collector/webpage_enrichment.py` 承担候选构造、Prompt user payload、选择结果清洗、多份文档同步等纯逻辑。
- `framework/openjiuwen/agent/collector_graph/webpage_enrichment.py` 只负责 session 状态、LLM/抓取调用、并发控制、日志和图节点写回。

日志：

- Info 日志记录候选数量、选中数量、最大 URL 数。
- 缓存事件使用 `metrics_json` 记录 `enrichment_cache_hit`、`enrichment_cache_insufficient`、`enrichment_cache_wait`、`enrichment_cache_stored` 和关闭前的 `enrichment_cache_summary`。记录条目数、原抓取正文字符串 `sys.getsizeof` 估算字节数与峰值；占用估算不包含分区视图和其他元数据，不代表进程总内存。日志不包含正文或凭据。
- `enrichment_fetch_started/succeeded/failed` 表示原三路流程的启动与结束；无运行时缓存上下文也记录这些事件。启动数不是 HTTP 请求数，回退、镜像竞速和重试仍可能发起多次 HTTP 请求。
- fetch 前 Info 日志记录待抓取 URL；记录前须确保本次运行的日志配置不是敏感模式。
- 正文更新成功后 Info 日志记录 `doc_id/source_id/status_code/raw_len/compressed_len/key_passages`，其中 key_passages 为数量。
- 正文更新与 `enrichment_unchanged` 分开记录；正文未变化只证明完成抓取和压缩，不证明新证据增加。
- 抓取失败、正文过短或压缩失败时记录原因和长度；httpx 直连路额外记录 `downloaded_bytes` / `download_ms` / `parse_ms`，用于判断阶段上限取值是否合适。
- DEBUG 级别新增 `enrichment_compression_debug` 的 input/output 两阶段日志。input 记录原抓取长度、实际送模的新旧正文长度、软目标和硬上限；output 记录模型原稿与最终正文长度、是否截断、是否与旧正文相同。output 表示压缩结果，是否写回须结合后续成功或未变化日志判断。
- 非敏感模式下上述 DEBUG 日志还输出实际送模正文、任务与研究约束、必保留事实、模型原稿和最终正文，不受普通日志头尾截断影响；敏感模式仅记录长度和处理标记，不输出正文或文档 ID。该日志进入 common 日志，不进入 metrics；在应用启动的 `LogManager.init` 设置 `level="DEBUG"`，非敏感模式需设置 `is_sensitive=False`，修改后重启进程生效。默认 INFO 不输出这些内容。
- 敏感模式下 fetch 和候选异常日志只保留固定事件分类与长度，不记录 URL、异常正文、事实锚点、标题或步骤文本。

## 数据契约与依赖

- collector 输入包含 `language`、`messages`、`section_idx`、`plan_idx`、`step_idx`、`max_search_query_count`、
  `max_research_loops`、`max_tool_call_turns_per_query`、`report_type`、`research_intent`。
- `InfoRetrievalNode` 从 `collector_context.language` 取得语言，并传入工具调用分支的 collector Prompt；该 Prompt 的输出语言约束使用此值。
- collector 输出至少包含 `history_queries`、`doc_infos`、`info_summary`、`evaluation`、`messages`。
- `EvidenceLedger` 记录 accepted/rejected/pending 证据、尝试过的 query 和缺口，供后续采集轮次判断。
- `CollectorContext.should_continue` 保存 supervisor 对下一轮检索价值的判断；为 `false` 时，collector 清空后续 query
  并进入 summary。
- 对有来源日期约束的 Tavily 结果，collector 记录 `raw`、`kept`、`filtered_out` 和 `date_unknown` 计数日志。日志不包含 query、URL、标题或正文。
- Tavily 工具的 ReAct tool message 使用域名与时间过滤后的规范化结果，避免模型根据已从正式证据删除的越界来源决定停止检索。
- `max_search_query_count` 来自 `config.info_collector_max_search_query_count`，表示单轮 query 硬上限；初始 query
  生成在需要外部检索时使用 `1..max_search_query_count`，明确不需要外部检索时可返回空列表；supervisor 后续
  query 在 `0..max_search_query_count` 范围内自主决定数量。
- `max_tool_call_turns_per_query` 来自 `config.info_collector_max_tool_call_turns_per_query`，独立于
  `max_research_loops` 生效。
- 搜索工具来源于 `web_search_context`、`local_search_context` 和 runtime API 工具配置。
- 只有 Tavily wrapper 将官方 `published_date` 归一化为 `source_date` 和 `source_date_type=published`；collector 只消费
  该 ISO 日期，不扫描 `date`、更新时间或其他 provider 字段。
- 网页正文增强节点只更新匹配 doc 的正文证据和证据引用，并同步 `history_queries` 供最终报告读取；证据保持来源语言，不承担报告本地化；不修改原始 URL、标题、评分或 evaluator 结果。

### 学术垂直搜索 Query 契约

- 初始 query 生成支持结构化 query item：每个 item 包含 `query` 和 `search_engine_name`。`search_engine_name`
  是 query 级 secondary vertical web engine，只能为 `pubmed`、`arxiv` 或空字符串，不替代用户配置的 primary
  web search engine。
- `search_engine_name=""` 表示明确不使用垂直源；query object 缺少该字段或旧格式字符串 query 时，collector 会按
  query 关键词启发式选择 `pubmed`、`arxiv` 或空值以保持向后兼容。
- 对 `pubmed` 和 `arxiv` 的 query，query 生成 Prompt 要求使用英文论文检索词、标准学术术语或 benchmark/算法名称；
  `missing_evidence` 等面向用户和报告链路的字段仍使用 collector 输入语言。
- direct web 分支会对每个 query 执行 primary web engine，并在存在 secondary vertical engine 且与 primary 不同时追加执行。
  当 `info_collector_search_method=all` 或配置了 `api_tools_config.collector_tools` 进入 LLM tool-calling 分支时，
  LLM 工具调用结束后也会确定性补跑该 query 的 secondary vertical engine。

### 学术垂直搜索失败降级流程

- direct web 分支中，primary web engine 和 secondary vertical engine 会并行执行。secondary vertical engine 失败时，不会重试
  secondary，也不会再次 fallback 调用 primary，只记录 fail-fast 事件并保留 primary 结果。
- LLM tool-calling 分支中，LLM 的 `web_search_tool` 调用始终使用 primary web engine。LLM 工具调用结束后，collector
  才根据 query item 的 `search_engine_name` 补跑 secondary vertical engine。如果本轮 LLM 已调用过 `web_search_tool`，该 secondary
  失败时不 fallback 到 primary，避免对同一 query 重复调用 primary；如果本轮 LLM 只调用了 local/custom 工具或未调用
  `web_search_tool`，该 secondary 失败时会 fallback 到 primary web engine。
- 单独调用 non-default vertical engine 时，如果请求启用可降级策略，则 PubMed/arXiv 返回 error 或抛出异常后，collector 记录
  vertical fallback 事件，并使用默认 web engine 对同一 query 重新执行一次。
- PubMed/arXiv 返回非法 XML、错误 XML 或非预期 root 时，wrapper 会抛出搜索响应类异常；`run_web_search` 会将其转换为
  `error` 结果，collector 再根据上述策略执行 fail-fast 或 primary fallback。

## 边界与错误处理

- collector 子图使用独立 workflow session，依赖驱动并发时避免共享子图状态。
- runtime API 响应大小、JSON 深度和容器长度限制由工具层保护。
- 网页抓取正文少于 200 字符或短于旧证据时不会直接覆盖旧证据；三路都未达到动态门槛时按抓取失败处理。
- 三条抓取路共用 `info_collector_webpage_enrich_fetch_timeout_seconds` 指定的整体 deadline；整体超时保留旧证据。
- httpx 直连路的下载与解析各自设阶段上限（下载 30 秒、解析 15 秒），下载另有字节上限（100 MB）；任一阶段超时视为该路失败，继续退到 Jina Reader 路。字节上限定位是 OOM 护栏而非截断策略——按字节截断会破坏 PDF 尾部的交叉引用表，使本可解析的文档解析失败。
- Jina Reader 路另有 30 秒阶段上限（`JINA_STAGE_TIMEOUT_SECONDS`）。provider 内部是固定 3 次重试 + 线性退避（不设界时最坏约 37 秒），自身不知道剩余预算，所以在节点侧单独设界：该上限取「30 秒」与「剩余预算向上取整」的较小值，因向上取整，多数情况下先到的仍是整体 deadline（事件记 `fetch_deadline_exceeded`，而非 `jina_fetch_failed`）。该上限同时作为 `budget` 传入 `fetch_page`——`asyncio.to_thread` 的底层线程不可取消，但 provider 会把内部重试、退避与单次请求超时都压进这个预算，线程收在预算附近（最坏再多花一次请求的 `connect + read`）。预算充裕时该预算不触发，行为与设界前逐字一致。
- PDF 由 httpx 直连路用 pypdfium2 本地解析；该路拿不到正文时，PDF 仍可由 Jina Reader 路转换。三路都返回 `%PDF-` 原始数据时按抓取失败处理。
- 压缩成功且正文非空即替换，即使结果未保留旧数字、单位或标识；Prompt 仍要求保留旧事实，但程序不执行后验门禁。压缩保持来源语言，不根据报告语言翻译证据。
- 空结果不会直接中断主图，但会通过 warning 进入章节和最终报告状态。
- 候选和网页内容不会插入 system prompt；其中的指令样文本只能作为不可信数据处理。
- 敏感日志模式下，采集输入、工具结果、中间消息、URL、异常正文和事实锚点不打印明文。
- query 生成 LLM 降级时沿用既有 fallback，不在代码侧检查或补写时间短语。

## 测试与验证

- `uv run pytest tests/framework/test_background_knowledge.py`
- `uv run pytest tests/info_collector/test_webpage_enrichment.py`
- `uv run pytest tests/info_collector/test_enrichment_quality.py tests/info_collector/test_enrichment_compression.py`
- `uv run pytest tests/info_collector/test_enrichment_research_constraints.py`
- `uv run pytest tests/info_collector/test_enrichment_content.py`
- `uv run pytest tests/info_collector/test_webpage_fetch_cache.py tests/framework/test_webpage_cache_lifecycle.py`
- `uv run pytest tests/info_collector/algorithm/test_tool_log.py`
- 网页增强测试覆盖 canonical URL 去重、Prompt 消息隔离、输出语言、三路抓取级联与整体 deadline、httpx 直连路各分支及其阶段上限、Jina 多 base 竞速与阶段上限、压缩成功直接写回、敏感日志脱敏、历史 query/最终报告同步和并发异常隔离。
- `uv run pytest tests/info_collector/test_academic_search_routing.py`
- `uv run pytest tests/info_collector/test_graph_builder.py::test_validate_query_count_accepts_structured_query_items`
- `uv run pytest tests/info_collector/test_graph_builder.py::test_collector_query_prompt_contract_uses_dynamic_max_query_count`
- 修改 runtime API 工具参与采集时，补充运行 `uv run pytest tests/tools/test_runtime_api.py`。
- 修改 web/local 工具映射时，补充运行 `uv run pytest tests/tools/test_web_search.py tests/tools/search_api/`。

## 相关文档

- [章节推理与写作子工作流](./section-reasoning-writing-sub-workflows.md)
- [搜索工具注册与运行时 API 工具](./search-tool-registration.md)
- [web fetch provider 注册](./web-fetch-provider-registry.md)
- [资料采集](../algorithm/research-collector.md)
