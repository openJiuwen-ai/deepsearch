# 错误码、异常与公共常量

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/common/` 下的统一错误码、异常基类与常用公共常量。

不覆盖各模块的完整异常处理流程；模块内如何捕获、包装和恢复应在对应 feature 文档中维护。

## 功能目的

`common` 为 algorithm、framework、utils、server 提供跨模块共享的错误表达和基础常量，使节点、工具、校验器和 API 能用一致的错误码、异常类型和消息格式传递失败原因。

## 可见行为

- `StatusCode` 的每个成员包含数字错误码和默认错误消息，可通过 `.code` 和 `.errmsg` 读取。
- `format_exception_info` 会生成 `[{code}]{message}` 格式的错误文本，并在消息模板含 `{e}` 时填入具体异常。
- `CustomException` 及其子类保留 `error_code` 和 `message` 属性，字符串形式为 `[{code}] {message}\t`。
- 业务代码会按语义选择 `CustomValueException`、`CustomRuntimeException`、`CustomIndexException` 等子类，以便测试和上层调用方区分失败类型。
- 公共常量提供语言标识、长度上限和完成反馈标记，供 URL、搜索内容、LLM 响应和用户反馈流程复用。

## 关键代码路径

- `openjiuwen_deepsearch/common/status_code.py`
- `openjiuwen_deepsearch/common/exception.py`
- `openjiuwen_deepsearch/common/common_constants.py`
- `openjiuwen_deepsearch/utils/validation_utils/field_validation.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/agent_factory.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/runtime_api/runtime_api.py`
- `tests/source_tracer/test_check.py`
- `tests/source_tracer/test_citation_verify_research.py`
- `tests/user_feedback_processor/test_rewrite.py`
- `tests/tools/test_runtime_api.py`

## 核心流程

1. 模块检测到参数、运行时、文件、索引或业务异常。
2. 代码选择对应 `StatusCode` 成员，并用 `.errmsg.format(...)` 或 `format_exception_info` 生成消息。
3. 需要抛出异常时，使用合适的 `Custom*Exception` 子类携带错误码和消息。
4. 节点、workflow 或 API 捕获异常后，把字符串消息、`error_code` 或格式化错误信息写入流式输出、最终结果或日志。

## 数据契约与依赖

- 错误码为 6 位数字。
- 前两位表示错误类型：`20` 为公共类型错误，`21` 为具体模块错误。
- 中间两位表示子类型；公共错误当前覆盖参数校验和文件相关错误，模块错误覆盖 agent、workflow、LLM、tool、节点、查询理解、人机交互、规划、采集、报告、溯源、模板、用户反馈和图表等域。
- 后两位表示同一子类型下的具体错误编号。
- `MAGIC_CODE` 当前为制表符，用作 `CustomException.__str__` 的尾部标记。
- 语言常量为 `CHINESE = "zh-CN"` 和 `ENGLISH = "en"`。
- 关键长度常量包括 `MAX_URL_LENGTH`、`MAX_QUERY_LENGTH`、`MAX_COLLECTOR_DOC_CONTENT_LENGTH`、`MAX_SEARCH_CONTENT_LENGTH` 和 `MAX_LLM_RESP_LENGTH`。
- `FINISH_TASK_FEEDBACK` 表示用户反馈处理中的完成任务标记。

## 边界与错误处理

- 不要复用已有错误码表达新语义；新增错误场景应按错误码分段补充 `StatusCode`。
- 不要随意改变 `CustomException.__str__` 的格式或尾部制表符，现有日志、断言和上层展示可能依赖该格式。
- `format_exception_info` 只对 `{e}` 占位做自动填充；其他命名占位需要调用方先格式化。
- 敏感场景应使用不打印细节的错误码或日志脱敏逻辑，避免把密钥、请求体或内部路径写入错误消息。
- 修改公共长度常量会影响 URL 校验、runtime API 响应归一化、搜索内容截断和 LLM 响应处理。

## 测试与验证

- `uv run pytest tests/tools/test_runtime_api.py`
- `uv run pytest tests/source_tracer/test_check.py`
- `uv run pytest tests/source_tracer/test_citation_verify_research.py`
- `uv run pytest tests/user_feedback_processor/test_rewrite.py`
- 修改参数校验错误码时，补充运行 `uv run pytest tests/server/test_deepsearch_run.py`。

## 相关文档

- [参数校验、安全目录与 URL 处理](../utils/validation-security-url.md)
- [Runtime API 工具配置](../config/runtime-api-tool-config.md)
- [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)
- [全局溯源](../algorithm/source-trace.md)
- [用户反馈处理](../algorithm/user-feedback-processor.md)
