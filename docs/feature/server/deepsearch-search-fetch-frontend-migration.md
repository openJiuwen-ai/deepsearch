# DeepSearch Telemetry API Frontend Migration

## 维护范围

本文档面向调用 `server.telemetry_event_server` 的前端或前端生成代理，说明 `POST /runs` 在 search/fetch 迁移后的请求契约、兼容策略和迁移步骤。

## 功能目的

DeepSearch runtime 已把 `search_fetch` 从固定的 “Serper + Jina” 组合迁移为：

- `web_search_engine_config` 选择 search provider
- `web_fetch_provider_config` 选择 fetch provider

前端需要按这个模型调整请求体和表单状态。

## 可见行为

- `POST /runs` 现在优先接收 `web_search_engine_config` 和 `web_fetch_provider_config`。
- `web_fetch_provider_config.provider_name` 需要显式提供。
- 当前 fetch provider 只支持 `jina`。
- 旧字段 `serper_api_key` 和 `jina_api_key` 仍可用，但只作为兼容 fallback。
- 如果 `tool_map=search_fetch` 且既没有新对象也没有旧 fallback，接口返回 `422`。

## 推荐请求形态

```json
{
  "query": "Find the official Python homepage and summarize it.",
  "search_mode": "react",
  "tool_map": "search_fetch",
  "llm": {
    "model_name": "gpt-4o-mini",
    "model_type": "openai",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-***"
  },
  "web_search_engine_config": {
    "search_engine_name": "serper",
    "search_api_key": "serper_***",
    "search_url": "",
    "max_web_search_results": 5,
    "extension": {}
  },
  "web_fetch_provider_config": {
    "provider_name": "jina",
    "api_key": "jina_***",
    "base_url": "",
    "extension": {}
  }
}
```

## 兼容请求形态

旧前端仍可继续发送：

```json
{
  "query": "Find the official Python homepage and summarize it.",
  "search_mode": "react",
  "tool_map": "search_fetch",
  "llm": {
    "model_name": "gpt-4o-mini",
    "model_type": "openai",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-***"
  },
  "serper_api_key": "serper_***",
  "jina_api_key": "jina_***"
}
```

服务端会自动映射为：

- `serper_api_key` -> `web_search_engine_config.search_engine_name="serper"`
- `jina_api_key` -> `web_fetch_provider_config.provider_name="jina"`

## 前端迁移建议

1. 先把前端内部状态模型改成两个独立对象：
   - `web_search_engine_config`
   - `web_fetch_provider_config`
2. UI 上不要再把 `search_fetch` 固定展示为 “Serper + Jina”。
3. fetch provider 选择器当前可以只提供 `jina`，但字段名保持通用。
4. HTTP adapter 先优先发送新对象；如果需要灰度，可保留旧字段 fallback 一段时间。
5. 前后端完全切换后，再删除 `jina_api_key` / `serper_api_key` 旧分支。

## 关键代码路径

- `server/telemetry_event_server.py`
- `tests/server/test_telemetry_event_server.py`
- `docs/feature/server/telemetry-event-server.md`

## 测试与验证

推荐先跑：

```bash
uv run pytest tests/server/test_telemetry_event_server.py
```

手工验证见 `docs/feature/server/telemetry-event-server.md` 和本次变更说明。
