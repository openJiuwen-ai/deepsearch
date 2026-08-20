# 学术搜索全文增强

## 目的

PubMed 和 arXiv 搜索结果在 `content` 中保留摘要或书目信息回退。每个 query 默认最多返回一条结果，搜索 wrapper 会尝试从官方开放来源获取该论文的全文。

学术垂直搜索默认关闭。只有在服务端请求的 `web_search_config` 中显式设置 `scholarly_search_enabled=true` 后，Collector 才会注册 PubMed 和 arXiv，并允许 query 级路由选择对应引擎。

## 行为与数据契约

- PubMed 通过 PMCID 从 PMC 获取 JATS XML 全文。
- arXiv 优先获取官方 HTML 全文，不可用时回退到官方 PDF。
- 全文单独保存在 `full_text` 中，`content` 始终保留摘要。
- 学术结果进入 Collector 时，可用的 `full_text` 会成为 evidence 的 `original_content`；全文不可用或获取失败时继续使用摘要。
- `content_type`、`full_text_url`、`full_text_format`、`full_text_status` 和 `full_text_truncated` 描述全文状态及来源。
- 全文缺失、获取失败或内容格式异常不会阻断正常搜索结果。
- wrapper 不改写模型生成的 query。
- PubMed ESearch、PubMed EFetch 和 PMC EFetch 在进程内跨 wrapper 实例共享请求调度。
- arXiv Atom API 调用共享请求调度，HTML/PDF 下载共享进程内并发上限 2；HTTP 429 冷却同时作用于两个路径。
- 学术 wrapper 对同一个 HTTP 请求只发送一次，不在内部重试。HTTP 429 的 `Retry-After` 最多记录为 30 秒进程内冷却，并作用于后续请求。
- PubMed 和 arXiv 作为 query 级 secondary engine 调用。搜索失败是否重试由 Collector 统一决定，最多尝试 3 次，因此不会与 wrapper 叠加成 `3 × 3` 次请求；不可重试错误只调用一次。
- PMC 全文和 arXiv HTML/PDF 获取失败时同样不重试，直接保留已有摘要并将 `full_text_status` 标记为 `failed`。arXiv HTML 不可用后尝试 PDF 属于备用来源切换，不是对同一个请求重试。
- arXiv HTML/PDF 下载允许重定向；构造旧版 arXiv PDF URL 时保留 archive category。

## 配置

`web_search_config` 支持以下配置：

- `scholarly_search_enabled`：是否启用 PubMed 和 arXiv 垂直搜索，默认 `false`；

学术引擎使用以下固定运行参数：获取全文；每次最多获取 `1` 条结果的全文；全文请求超时为 `30` 秒；全文最大字符数使用 Collector 文档内容上限；PubMed ESearch、PubMed EFetch、PMC EFetch 以及 arXiv Atom API 的请求速率均为每 `3` 秒 `1` 次。

学术搜索结果数默认也是 `1`。调用方构造 wrapper 时仍可通过 `max_web_search_results` 显式覆盖。

## 关键代码与测试

- `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/pubmed.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/arxiv.py`
- `openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`
- `openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`
- `tests/tools/search_api/test_scholarly_search.py`
- `tests/info_collector/algorithm/test_collector_function.py`
- `tests/info_collector/algorithm/test_collector_evidence.py`
