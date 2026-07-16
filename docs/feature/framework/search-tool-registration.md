# 搜索工具注册与运行时 API 工具

## 维护范围

本文档覆盖 framework 层 web/local 搜索工具注册、外部自定义工具加载、域名约束合并、QPS 限流和 runtime API 工具生成。

不覆盖具体搜索引擎服务的外部 API 语义；只记录本仓库对这些工具的包装契约。

## 功能目的

搜索工具注册让研究工作流和 DeepSearch 能通过统一 openJiuwen `LocalFunction` 调用 web、本地和用户配置的 runtime API 工具，同时把实例放进
contextvar，避免节点直接持有全局工具对象。

## 可见行为

- Agent 运行开始时根据 `AgentConfig` 初始化 web/local 搜索引擎实例。
- 支持内置 web 引擎：tavily、google/serper、xunfei、petal、bocha、jina、perplexity。
- 支持内置 local 引擎：openapi、native。
- 支持通过 `custom_*_search_file` 和 `custom_*_search_func` 注册自定义搜索工具。
- Tavily 支持把意图识别出的 include/exclude domains 追加到已有配置。
- web 搜索调用受 `web_search_max_qps` 限流。
- runtime API 配置会动态生成工具 schema，并可把搜索型响应转换为 collector 可消费 payload。

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

1. `DeepresearchAgent._initialize_tools` 读取 `custom_web_search_config`、`custom_local_search_config` 和搜索引擎配置。
2. `_register_web_search_tool` / `_register_local_search_tool` 更新内置 mapping，并检查目标引擎是否存在。
3. 搜索实例写入 `web_search_context` 和 `local_search_context`。
4. web 搜索工具通过 `run_web_search` 从 context 取实例并调用 `aresults`。
5. local 搜索工具通过 `run_local_search` 从 context 取实例并调用 `aresults`。
6. runtime API 工具由 `build_runtime_api_tools` 按配置生成 `LocalFunction`，工具调用时发送 HTTP 请求并校验响应。
7. workflow 结束时重置 context token，并关闭支持 `aclose` 的本地搜索引擎。

## 数据契约与依赖

- web/local openJiuwen 工具输入均包含 `query` 和 `search_engine_name`。
- web/local 工具输出包含 `search_engine` 和 `search_results`；异常时还包含 `error`。
- native local search 必须配置 `knowledge_base_configs`。
- runtime API 参数按 `send_method` 写入 header、query 或 JSON body；`none` 参数进入 body 但不参与 required 发送校验。
- runtime API 响应默认读取 JSON；`response_wrapper=search_result` 时会归一化为 `search_results`。

### 学术垂直搜索引擎契约

- 内置 web engine 包含 `pubmed` 和 `arxiv`。它们通过统一 `web_search_tool` 暴露，通常由 collector query item 的
  `search_engine_name` 作为 secondary vertical engine 触发。
- PubMed wrapper 位于 `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/pubmed.py`，
  arXiv wrapper 位于 `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/arxiv.py`，
  共享默认 URL、XML namespace、provider 级限流和退避工具位于 `scholarly_search/common.py`。
- PubMed wrapper 使用 `ESearch -> EFetch XML`。返回 item 的 `content` 优先使用 abstract 或 structured abstract；
  无 abstract 时才退回期刊、发布日期和作者等书目信息。
- arXiv wrapper 使用 Atom API。返回 item 的 `content` 使用论文 summary，`url` 使用 arXiv entry id。
- PubMed 内部按 E-utilities request 级限流：无 API key 默认 3 req/s，有 API key 默认 10 req/s。arXiv 内部按 3 秒请求间隔限流。
  HTTP 429、PubMed rate-limit payload 或异常响应会作为搜索错误返回给上层，由 collector 根据 primary/secondary 策略决定 retry、
  fail-fast 或 fallback。

## 边界与错误处理

- 找不到 web/local 引擎实例时分别抛出 `WEB_SEARCH_INSTANCE_OBTAIN_ERROR` 或 `LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR`。
- 搜索引擎调用异常不会抛出到 workflow，而是返回空 `search_results` 和错误文本。
- runtime API URL 会经过 `validate_runtime_request_url`，避免不安全请求目标。
- runtime API 响应大小限制为 2 MiB，JSON 深度限制为 20，单个对象或数组最多 1000 项。
- 重名 runtime API 工具合并时保留已有工具并记录 warning。

## 测试与验证

- `uv run pytest tests/tools/test_web_search.py`
- `uv run pytest tests/tools/test_web_search_rate_limit.py`
- `uv run pytest tests/tools/test_runtime_api.py`
- `uv run pytest tests/tools/search_api/test_scholarly_rate_limit.py`
- `uv run pytest tests/tools/search_api/test_external_import_tool.py`
- 修改具体搜索引擎 wrapper 时，运行 `uv run pytest tests/tools/search_api/`。

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [信息采集子图](./info-collector-subgraph.md)
- [DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)
- [资料采集](../algorithm/research-collector.md)
