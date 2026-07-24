# DeepSearch 网页抓取 Provider 注册

## 维护范围

本文档覆盖 framework 层 `fetch_api` 的网页抓取 provider 注册与解析契约，以及 DeepSearch `web_fetch` 工具如何消费该注册表。

不覆盖 web search 引擎注册、runtime API 工具，也不覆盖 goal 相关 LLM 摘要 Prompt 内容；见相关文档。

## 功能目的

网页抓取 provider 注册把 DeepSearch `search_fetch` 模式的单页获取/提取从固定 Jina 客户端解耦为可扩展的 provider 映射。调用方通过 `web_fetch_provider_config` 显式选择 provider；算法层 `WebFetch` facade 负责批量 URL、统一错误文案和 goal 摘要，provider 只负责返回原始页面文本。

## 可见行为

- `resolve_web_fetch_provider` 按 `provider_name` 从内置 mapping 构造 provider 实例。
- 当前内置 provider 只有 `jina`。
- `provider_name` 为空或未知时不会静默回退到默认 provider；`WebFetch` 返回受控工具错误字符串。
- Jina provider 按配置 `base_url`、环境变量 `JINA_READER_BASE_URL` 和内置默认 reader 地址解析候选端点，并对多个 base 并发请求。
- DeepSearch `search` / `react` 在 `tool_map=search_fetch` 时，把 `agent_config.web_fetch_provider_config` 传给 `WebFetch` 初始化。
- 顶层已退役的 `jina_api_key` / `serper_api_key` 字段不再接受；应分别使用 `web_fetch_provider_config` 和 `web_search_engine_config`。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/tools/fetch_api/base.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/fetch_api/registry.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/fetch_api/jina/api_wrapper.py`
- `openjiuwen_deepsearch/algorithm/search_tools/web_fetch_tool.py`
- `openjiuwen_deepsearch/config/config.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `tests/search_agent/test_web_fetch_provider_registry.py`
- `tests/search_agent/test_jina_reader_endpoints.py`
- `tests/search_agent/test_web_search_tool_adapter.py`

## 核心流程

1. 调用方在 `agent_config.web_fetch_provider_config` 中设置 `provider_name`、`api_key`、可选 `base_url` / `extension`。
2. DeepSearch 工作流把该配置写入搜索工具初始化字典。
3. `WebFetch` 调用 `resolve_web_fetch_provider` 归一化名称并实例化 provider。
4. 工具执行时 provider 的 `fetch_page(url)` 返回原始页面文本。
5. `WebFetch` 对成功内容做 goal 相关 LLM 摘要，并把历史兼容文本写回工具消息。
6. 搜索运行结束后清零 `web_fetch_provider_config.api_key`。

## 数据契约与依赖

- `WebFetchProviderConfig.provider_name`：当前有效值为 `jina`；空字符串表示未配置。
- `WebFetchProviderConfig.api_key`：`bytearray` 密钥；使用后由工作流清零。
- `WebFetchProviderConfig.base_url`：可选；Jina 会优先使用该地址，再叠加 `JINA_READER_BASE_URL` 和默认 `https://r.jinaai.cn`、`https://r.jina.ai`。
- `WebFetchProviderConfig.extension`：预留给未来 provider 扩展；当前 Jina 实现忽略该字段。
- Provider 协议为 `BaseWebFetchProvider`：必须暴露 `provider_name` 和同步 `fetch_page(url) -> str`。
- 算法层 `web_search` 与 fetch 解耦：搜索走 `web_search_engine_config` + `web_search_context`；抓取只走本注册表。

## 边界与错误处理

- 未设置 `provider_name` 时，`web_fetch` 返回提示显式配置的错误字符串，不发起网络请求。
- 未知 `provider_name` 时返回受支持 provider 列表，不抛出到搜索循环外。
- Jina reader 认证失败或全部端点失败时返回 `[web_fetch] Failed to read page.` 一类受控失败文本。
- 新增 provider 时需同时更新 `fetch_provider_mapping`、配置文档、telemetry `/runs` schema 校验和对应单测。
- 不要把密钥写入日志或 feature/用户文档示例的真实值中。

## 测试与验证

```bash
uv run pytest tests/search_agent/test_web_fetch_provider_registry.py
uv run pytest tests/search_agent/test_jina_reader_endpoints.py
uv run pytest tests/search_agent/test_web_search_tool_adapter.py
uv run pytest tests/server/test_telemetry_event_server.py
```

## 相关文档

- [搜索工具注册与运行时 API 工具](./search-tool-registration.md)
- [Search Tools](../algorithm/search-agent/search-tools.md)
- [DeepSearch 搜索工作流配置](../config/search-workflow-config.md)
- [Agent 与服务运行配置](../config/agent-and-service-config.md)
- [遥测事件服务](../server/telemetry-event-server.md)
