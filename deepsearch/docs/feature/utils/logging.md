# 日志与接口记录

## 维护范围

本文档覆盖 `utils/log_utils/` 下的通用日志、接口日志、metrics 日志、日志轮转权限、日志路径安全和敏感信息开关。

不覆盖节点格式化 debug 日志；该部分见 [调试与中间结果导出](./debug-and-export.md)。

## 功能目的

日志工具为 SDK、server 和 workflow 节点提供统一日志初始化入口，确保日志目录受控、文件权限安全、长消息可截断、接口调用可审计，
同时在敏感模式下减少明文泄露。

## 可见行为

- `LogManager.init` 只初始化一次。
- `log_dir=None` 时输出到控制台；传入目录时输出到 `common/`、`metrics/`、`interface/` 等子目录。
- 日志目录必须位于安全基目录 `./output/logs` 下。
- 活跃日志文件权限为 `0o640`，轮转文件权限为 `0o440`，目录权限为 `0o750`。
- 常见第三方 logger 默认压到 warning 级别。
- 通用日志注入 `session_id`，并截断超长消息。
- `record_interface_log` 输出角色、session、API 名称、耗时、成功状态和响应信息。

## 关键代码路径

- `openjiuwen_deepsearch/utils/log_utils/log_manager.py`
- `openjiuwen_deepsearch/utils/log_utils/log_common.py`
- `openjiuwen_deepsearch/utils/log_utils/log_handlers.py`
- `openjiuwen_deepsearch/utils/log_utils/log_interface.py`
- `openjiuwen_deepsearch/utils/log_utils/log_metrics.py`
- `tests/utils/test_log_manager.py`
- `tests/utils/test_log_interface.py`
- `tests/server/test_request_logging.py`

## 核心流程

1. 调用方调用 `LogManager.init` 并传入目录、级别、轮转大小、备份数量和敏感模式。
2. `LogManager` 校验参数和日志目录安全性。
3. 初始化 common、metrics、interface logger。
4. 如果 `node_debug_enable=True`，额外初始化节点 debug logger。
5. 设置第三方 logger 级别，并记录当前敏感模式和日志目录。
6. 业务入口通过 `session_id_ctx` 给通用日志添加 session 维度。

## 数据契约与依赖

- `level` 只能是 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。
- `max_bytes` 范围为 0 到 1000 MiB。
- `backup_count` 范围为 0 到 1000。
- interface 日志格式为 `role | session_id | api_name | duration_min | result | response_info_json`。
- metrics 日志使用 `TIME_LOGGER_TAG` 标记耗时打点。

## 边界与错误处理

- `is_sensitive` 必须是 bool，否则抛参数校验错误。
- 不安全日志目录抛 `PARAM_CHECK_ERROR_LOG_DIR_UNSAFE`。
- 目录解析失败抛 `PARAM_CHECK_ERROR_LOG_DIR_INVALID`。
- handler 关闭失败时，敏感模式下不打印异常详情。
- 日志截断只影响主消息，不删除异常堆栈。

## 测试与验证

- `uv run pytest tests/utils/test_log_manager.py`
- `uv run pytest tests/utils/test_log_interface.py`
- 修改 server 请求日志链路时，补充运行 `uv run pytest tests/server/test_request_logging.py`。

## 相关文档

- [调试与中间结果导出](./debug-and-export.md)
- [报告研究主工作流](../framework/research-workflow.md)
