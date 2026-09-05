# 参数校验、安全目录与 URL 处理

## 维护范围

本文档覆盖字段校验、入口参数校验、secret 清零、安全目录创建、URL 规范化、域名提取、runtime API/embedding/搜索服务/学术全文 URL SSRF 防护。四类 URL 校验（runtime API、embedding 服务、搜索服务 `search_url`、学术全文 `validate_scholarly_full_text_url`）共享底层 `_is_non_public_ip` 判定逻辑，统一拦截 CGNAT 段、私网、回环等非公网地址。

不覆盖 server API schema 的 Pydantic 模型；该部分属于 `server/` 或 `config/` owner。

## 功能目的

这些工具把通用输入校验和安全边界集中到一处，避免各入口重复实现字符串长度、conversation_id、目录逃逸、URL scheme 和内网地址校验。

## 可见行为

- 字符串字段会校验类型和长度。
- `agent_config` 必须包含 `execute_mode`、`llm_config`、`info_collector_search_method`，并至少提供 web 或 local 搜索配置。
- `run_agent` 的 `conversation_id` 只能包含字母、数字、下划线和连字符，最长 128 字符。
- `interrupt_feedback` 只允许空值、`accepted`、`cancel`、`revise_outline`、`revise_comment`。
- `zero_secret` 会原地清零 bytearray。
- 安全目录必须位于指定 safe base 下，并设置 `0o750` 权限。
- runtime API、embedding 服务、用户配置的搜索服务 URL（`search_url`）和学术全文下载 URL（`validate_scholarly_full_text_url`，无旁路开关）默认禁止 localhost、私有地址、保留地址、非公网地址（含 CGNAT 段 100.64.0.0/10，覆盖阿里云 ECS 元数据端点 100.100.100.200）和非 http/https scheme。对 IPv4-mapped IPv6 地址（如 `::ffff:100.100.100.200`），会提取底层 IPv4 地址后再次校验。

## 关键代码路径

- `openjiuwen_deepsearch/utils/validation_utils/field_validation.py`
- `openjiuwen_deepsearch/utils/validation_utils/param_validation.py`
- `openjiuwen_deepsearch/utils/common_utils/security_utils.py`
- `openjiuwen_deepsearch/utils/common_utils/url_utils.py`
- `openjiuwen_deepsearch/utils/common_utils/embedding_utils.py`
- `tests/utils/test_url_utils.py`
- `tests/server/test_deepsearch_run.py`
- `tests/tools/test_runtime_api.py`

## 核心流程

1. 入口调用参数校验函数，先检查必填和类型，再检查长度、枚举值或安全格式。
2. 目录类输入使用 `Path.resolve` 解析真实路径，并确认位于 safe base 内。
3. URL 先做 scheme、长度、域名和路径规范化。
4. HTTP 服务 URL 解析 host；IP 直接检查，域名先 DNS 解析再检查所有地址。
5. 检测到不安全 URL 时抛 `PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR`。
6. 本地调试可通过显式环境变量放宽 runtime API 或 embedding URL 校验。

## 数据契约与依赖

- `SAFE_CONVERSATION_ID_PATTERN` 为 `^[A-Za-z0-9_-]{1,128}$`。
- URL 允许 scheme 为 `http` 和 `https`。
- `MAX_URL_LENGTH` 控制 URL 路径处理最大长度。
- `RUNTIME_API_ALLOW_UNSAFE_URL=1|true|yes` 可放宽 runtime API URL 校验。
- `EMBEDDING_SERVICE_ALLOW_UNSAFE_URL=1|true|yes` 可放宽 embedding 服务 URL 校验。
- `SEARCH_SERVICE_ALLOW_UNSAFE_URL=1|true|yes` 可放宽用户配置搜索服务 URL（`search_url`）校验。**使用 `local_search_api` 或其他内网/自托管搜索引擎时必须设置此环境变量**，否则创建、更新和执行路径均会因 SSRF 校验失败而返回 HTTP 400。该开关为进程级全局配置。
- `EMBEDDING_SSL_VERIFY` 和 `EMBEDDING_SSL_CERT` 控制 embedding 请求证书校验参数。

## 边界与错误处理

- 缺字段、空字段、类型错误、长度越界分别使用对应 `StatusCode`。
- URL host 解析失败会按请求参数错误抛出。
- DNS 返回任一非公网地址时，整个 URL 判定为不安全。
- 已知限制：校验只覆盖首次请求目标；HTTP 客户端跟随 302 重定向或校验后 DNS 解析结果变化（DNS rebinding）可绕过地址校验，属既有共性缺口。
- `normalize_url` 在解析失败时返回原始 URL，不把规范化失败升级为异常。
- `ensure_safe_directory` 会创建目录并强制 chmod，避免 umask 改变权限。

## 测试与验证

- `uv run pytest tests/utils/test_url_utils.py`
- 修改 runtime API URL 校验时，补充运行 `uv run pytest tests/tools/test_runtime_api.py`。
- 修改 Agent 入口参数校验时，补充运行 `uv run pytest tests/server/test_deepsearch_run.py`。

## 相关文档

- [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)
- [LLM 运行时封装](../llm/llm-runtime.md)
