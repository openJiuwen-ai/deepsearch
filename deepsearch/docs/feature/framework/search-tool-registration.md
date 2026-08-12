# 搜索工具注册与运行时 API 工具

## 维护范围

本文档覆盖 framework 层 web/local 搜索工具注册、外部自定义工具加载、域名约束合并、QPS 限流和 runtime API 工具生成。

不覆盖具体搜索引擎服务的外部 API 语义；只记录本仓库对这些工具的包装契约。DeepSearch 网页抓取 provider 注册见 [网页抓取 Provider 注册](./web-fetch-provider-registry.md)。

## 功能目的

搜索工具注册让研究工作流和 DeepSearch 能通过统一 openJiuwen `LocalFunction` 调用 web、本地和用户配置的 runtime API 工具，同时把实例放进
contextvar，避免节点直接持有全局工具对象。

## 可见行为

- Agent 运行开始时根据 `AgentConfig` 初始化 web/local 搜索引擎实例。
- 支持内置 web 引擎：tavily、google、serper、xunfei、petal、bocha、jina、perplexity，以及研究工作流的 secondary 引擎 pubmed、arxiv。
- 支持内置 local 引擎：openapi、native。
- 支持通过 `custom_*_search_file` 和 `custom_*_search_func` 注册自定义搜索工具。
- Tavily 支持把意图识别出的 include/exclude domains 追加到已有配置。
- DeepResearch 的入口预搜索完成后，Tavily 可按 `source_date` 接收绝对起止日期；`content_date` 不发送原生日期参数。
- web 搜索的每个 provider HTTP 请求受 `web_search_max_qps` 限流。
- DeepSearch `search` / `react` 模式会先把活动 web search wrapper 注册到同一个 `web_search_context`，再执行 `web_search` adapter。
- runtime API 配置会动态生成工具 schema，并可把搜索型响应转换为 collector 可消费 payload；其结果不参与来源日期过滤。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/web_search.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/local_search.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/runtime_api/runtime_api.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/runtime_api/api_wrapper.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/`
- `openjiuwen_deepsearch/config/runtime_api_models.py`
- `tests/tools/test_web_search.py`
- `tests/tools/test_runtime_api.py`
- `tests/tools/search_api/test_external_import_tool.py`

## 核心流程

1. `DeepresearchAgent._initialize_tools` 与 DeepSearch 搜索模式共享 web search wrapper 初始化逻辑，读取 `custom_web_search_config`、`custom_local_search_config` 和搜索引擎配置。
2. `register_web_search_tool` / `_register_local_search_tool` 更新内置 mapping，并检查目标引擎是否存在。
3. 研究工作流会注册活动 web 引擎及 PubMed、arXiv；DeepSearch 仅注册活动引擎。搜索实例写入 `web_search_context` 和 `local_search_context`。
4. web 搜索工具通过 `run_web_search` 从 context 取实例并调用 `aresults`。
5. local 搜索工具通过 `run_local_search` 从 context 取实例并调用 `aresults`。
6. runtime API 工具由 `build_runtime_api_tools` 按配置生成 `LocalFunction`，工具调用时发送 HTTP 请求并校验响应。
7. workflow 结束时重置 context token，并关闭支持 `aclose` 的本地搜索引擎。

## 数据契约与依赖

- web/local openJiuwen 工具输入均包含 `query` 和 `search_engine_name`。
- 统一 web ToolCard 和调用签名不增加时间字段。时间范围写入当前会话的 Tavily wrapper，不使用相对当前时间的
  `time_range`。
- Tavily 的绝对日期参数按发表日期或最后更新时间过滤；结果随后仍由 collector 按统一发表日期过滤。
- 开始和结束日期分别向前、向后移动一天，以适配 Tavily 严格 `after`/`before` 与内部包含边界；`date.min`、`date.max`
  等无实际收窄作用的极值不下推。
- 不同 workflow 运行使用独立 `web_search_context` 实例，时间状态不跨会话共享。HITL 恢复会创建新 wrapper，
  因此接受大纲或达到交互轮次上限后会从 session 重新应用域名和时间约束。
- web/local 工具输出包含 `search_engine` 和 `search_results`；异常时还包含 `error`。
- Tavily `news` 结果中的 RFC 2822 或 ISO `published_date` 在 wrapper 边界归一化为 UTC ISO 日期；官方并不保证
  `general` 主题返回该字段，缺失日期仍按未知来源处理。
- DeepSearch 的 `algorithm/search_tools/web_search_tool.py` 不再自行选定 provider，而是从 `web_search_context` 解析当前活动实例并复用其 `search_results`。
- native local search 必须配置 `knowledge_base_configs`。
- runtime API 参数按 `send_method` 写入 header、query 或 JSON body；`none` 参数进入 body 但不参与 required 发送校验。
- runtime API 响应默认读取 JSON；`response_wrapper=search_result` 时会归一化为 `search_results`。

### 学术垂直搜索引擎契约

- 内置 web engine 包含 `pubmed` 和 `arxiv`。它们通过统一 `web_search_tool` 暴露，通常由 collector query item 的
  `search_engine_name` 作为 secondary vertical engine 触发。
- PubMed wrapper 位于 `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/pubmed.py`，
  arXiv wrapper 位于 `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/arxiv.py`，
  共享默认 URL、XML namespace 和响应辅助工具位于 `scholarly_search/common.py`。
- PubMed wrapper 使用 `ESearch -> EFetch XML`。返回 item 的 `content` 优先使用 abstract 或 structured abstract；
  无 abstract 时才退回期刊、发布日期和作者等书目信息。
- arXiv wrapper 使用 Atom API。返回 item 的 `content` 使用论文 summary，`url` 使用 arXiv entry id。
- PubMed 的 ESearch、PubMed EFetch 和 PMC EFetch 在进程内跨 wrapper 实例共享请求间隔；arXiv Atom API 同样共享请求间隔，
  HTML/PDF 全文下载在进程内共享并发上限 2。429 冷却会同时约束对应 provider 的后续请求。
- HTTP 429、500、502、503、504、连接错误和超时最多尝试 3 次；429 优先遵守最长 30 秒的 `Retry-After`，
  其余临时错误按 1 秒、2 秒退避。其他 4xx、错误 payload 和内容解析错误不做网络重试，耗尽后交由 collector 的
  primary/secondary 策略决定 fail-fast 或 fallback。统一 web 搜索工具的 `web_search_max_qps` 仍约束顶层工具调用频率。

## 边界与错误处理

- 找不到 web/local 引擎实例时分别抛出 `WEB_SEARCH_INSTANCE_OBTAIN_ERROR` 或 `LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR`。
- 搜索引擎调用异常不会抛出到 workflow，而是返回空 `search_results` 和错误文本。
- runtime API URL 会经过 `validate_runtime_request_url`，避免不安全请求目标。
- runtime API 响应大小限制为 2 MiB，JSON 深度限制为 20，单个对象或数组最多 1000 项。
- 重名 runtime API 工具合并时保留已有工具并记录 warning。
- 非 Tavily web 引擎不接收原生绝对日期参数，也不参与来源日期过滤。

## 测试与验证

- `uv run pytest tests/tools/test_web_search.py`
- `uv run pytest tests/tools/test_web_search_rate_limit.py`
- `uv run pytest tests/tools/test_runtime_api.py`
- `uv run pytest tests/tools/search_api/test_scholarly_search.py`
- `uv run pytest tests/tools/search_api/test_external_import_tool.py`
- 修改具体搜索引擎 wrapper 时，运行 `uv run pytest tests/tools/search_api/`。

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [信息采集子图](./info-collector-subgraph.md)
- [DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)
- [DeepSearch 网页抓取 Provider 注册](./web-fetch-provider-registry.md)
- [资料采集](../algorithm/research-collector.md)
