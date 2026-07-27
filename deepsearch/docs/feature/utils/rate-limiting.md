# QPS 限流

## 维护范围

本文档覆盖异步 QPS 限流器和 `qps_rate_limit_async` 装饰器。

不覆盖具体搜索引擎 wrapper 的 API 行为；工具注册见 [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)。

## 功能目的

QPS 限流工具用于控制 web 搜索等外部请求的并发速率，避免超过供应商限制或触发短时间突发请求。

## 可见行为

- `set_max_qps(None)`、`0` 或负数时不启用限流。
- 支持整数和浮点 QPS，例如 `0.5` 表示每 2 秒 1 个请求。
- QPS 大于等于 1 时按每秒令牌桶限流。
- QPS 小于 1 时按更长 time period 创建 limiter。
- 获取许可超时后会重试一次；仍失败则抛 `CustomRuntimeException`。
- web 搜索工具通过装饰器在实际调用前自动获取许可。

## 关键代码路径

- `openjiuwen_deepsearch/utils/rate_limiter_utils/qps_limiter.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/web_search.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `tests/utils/test_qps_limiter_utils.py`
- `tests/tools/test_web_search.py`

## 核心流程

1. Agent 初始化搜索工具时调用 `qps_rate_limiter.set_max_qps(agent_config.web_search_max_qps)`。
2. 被 `qps_rate_limit_async` 装饰的异步函数执行前调用 `acquire`。
3. `acquire` 根据当前 QPS 获取或创建 `AsyncLimiter`。
4. 计算等待超时时间，并尝试获取令牌。
5. 首次超时记录 warning 并重试。
6. 第二次超时抛 `RATE_LIMIT_TIMEOUT_ERROR`。

## 数据契约与依赖

- 全局实例为 `qps_rate_limiter`。
- timeout 计算范围为 3 到 60 秒。
- QPS 小于等于 0 或为空表示禁用。
- 限流底层依赖 `aiolimiter.AsyncLimiter`。

## 边界与错误处理

- 当前实现只在 `_limiter is None` 时创建 limiter；修改 QPS 后如果已有 limiter，需注意旧 limiter 复用行为。
- 超时异常会包装为 `CustomRuntimeException`。
- 装饰器只支持 async function。

## 测试与验证

- `uv run pytest tests/utils/test_qps_limiter_utils.py`
- `uv run pytest tests/tools/test_web_search.py`

## 相关文档

- [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)
