# 学术搜索与全文增强

## 功能边界

Research Collector 可以在用户配置的主 Web 搜索引擎之外，为同一个 `RetrievalQuery` 调用 PubMed、arXiv 和 Semantic Scholar。学术搜索默认关闭，只有 `scholarly_search_enabled=true` 时才注册三个 provider；普通 Web 引擎 extension 中的旧同名开关不会启用该功能。

本功能只扩展学术来源检索、查询内融合和开放全文补全，不改变用户查询中的时间约束语义。学术结果保留 provider 返回的发布日期元数据，但不会新增学术来源日期过滤。

## 请求配置

服务端 JSON 请求中的 API Key 使用普通字符串。请求模型校验后，Manager 会在内部 Agent 配置边界将其转换为可清零的 `bytearray`；不要把 key 放进 URL、查询文本或日志字段。

```json
{
  "web_search_config": {
    "web_search_config_id": 1,
    "max_web_search_results": 5,
    "scholarly_search_enabled": true,
    "scholarly_search_config": {
      "fetch_full_text": false,
      "max_full_text_results_per_query": 1,
      "pubmed": {
        "search_api_key": "${PUBMED_API_KEY}",
        "max_search_results": 1,
        "requests_per_second": 0.3333333333,
        "email": "research@example.com",
        "tool": "openjiuwen-deepsearch"
      },
      "arxiv": {"max_search_results": 1, "requests_per_second": 0.3333333333},
      "semantic_scholar": {
        "search_api_key": "${SEMANTIC_SCHOLAR_API_KEY}",
        "max_search_results": 1
      }
    }
  }
}
```

根级可配置项只有：

- `fetch_full_text`：是否在融合后获取开放全文，默认 `true`。
- `max_full_text_results_per_query`：每条 query 融合后的统一全文 Top-N，默认 `1`，范围 `0..10`。

每个 provider 可独立配置 `search_url`、`search_api_key`、`max_search_results` 和 `requests_per_second`；PubMed 还支持 NCBI 要求的 `email` 与 `tool`。默认速率为 PubMed/arXiv 每秒 `1/3`、Semantic Scholar 每秒 `0.5`。

下载大小、正文长度、下载/解析超时、PDF 页数和重定向次数属于实现及安全边界，使用代码内固定默认值，不属于公共请求配置。未知配置项会在模型边界被拒绝。旧的 `scholarly_search_engine_configs` 和 Web extension 中的 `scholarly_*` 参数不再支持。

## 查询与路由契约

- `RetrievalQuery.primary_engine` 是主 Web 引擎；`secondary_engines` 是有序、去重后的学术引擎，最多三个，且不会重复主引擎。
- `max_tool_call_turns_per_query` 同时作为每条 query 的逻辑搜索调用预算，字段名称保持不变。主 Web 引擎优先占用一个额度，剩余额度按 `secondary_engines` 顺序使用；同一次逻辑调用内部的 HTTP 重试不重复占用额度。
- 普通学术查询路由 Semantic Scholar；医学查询优先 PubMed；技术查询优先 arXiv；医学与技术交叉查询依次使用 PubMed、arXiv、Semantic Scholar。
- 直接 Web 路径并发执行聚合后的引擎计划。LLM tool-calling 路径保留原有循环，并在循环结束后执行辅助引擎组。
- secondary 只要有一个返回非空结果便不回退；全部失败或为空、且 LLM 尚未调用 Web 时，主引擎最多回退一次。

## 融合与全文流程

1. 收集同一 query 的主引擎及学术引擎结果。
2. 使用 DOI、PMID、PMCID、arXiv/provider ID 和规范化 URL 等身份信息融合重复论文，同时保留命中来源。
3. 保持融合结果的稳定输入顺序，不使用 provider score 或额外 LLM 分数重新排序。
4. 从含全文候选地址的论文中按该顺序选择前 `max_full_text_results_per_query` 篇。
5. 获取全文后重建 evidence、`content_ref`、关键段落和 source store；失败时保留摘要，不阻断搜索结果。

全文保存在 `full_text`，摘要继续保存在 `content`。`full_text_url`、`full_text_format`、`full_text_status` 和 `full_text_truncated` 描述全文状态。全文下载会校验目标 URL 和每次重定向，并限制下载大小、解析时间及 PDF 页数。

Provider 行为：

- PubMed 使用 `ESearch -> EFetch XML`，并可通过 PMCID 从 PMC 获取 JATS XML。PubMed API Key 按 NCBI API 约定进入请求参数，但 HTTP/传输异常会转换成不包含请求 URL 的安全错误。
- arXiv 使用 Atom API，优先尝试官方 HTML，失败后尝试官方 PDF。
- Semantic Scholar 返回规范化论文元数据及开放全文候选地址。
- PubMed、arXiv 和 Semantic Scholar 的每个 HTTP 请求只尝试一次。学术搜索失败会标记为不可重试，collector 不会再次调用该学术引擎。

## 验证范围

主要实现与回归测试位于：

- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/graph_builder.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/info_collector.py`
- `openjiuwen_deepsearch/algorithm/research_collector/scholarly_fusion.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/`
- `tests/info_collector/test_academic_search_routing.py`
- `tests/info_collector/algorithm/test_scholarly_fusion.py`
- `tests/tools/search_api/test_scholarly_search.py`
- `tests/tools/search_api/test_scholarly_full_text.py`
