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
- 通用日志按运行分隔: 路径为 `common/YYYYMMDD/common_YYYYMMDD_HHMMSS_hash.log`，每次调用 `LogManager.new_run()` 创建新文件。
- warning 日志同样按运行分隔: `common/YYYYMMDD/common_warning_YYYYMMDD_HHMMSS_hash.log`，与同次运行的 common 日志共享 hash。
- metrics 日志同样按运行分隔: `metrics/YYYYMMDD/metrics_YYYYMMDD_HHMMSS_hash.log`，与同次运行的 common 日志共享 hash；init 时创建全量文件，每次 `new_run()` 创建 per-run 文件。
- SDK (`main.py`) 和 Server (`deepsearch_run.py` / `telemetry_event_server.py`) 每次运行都创建 per-run 文件，通过 `RunIdFilter` + contextvar 实现并发隔离。
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
- `server/routers/deepsearch_run.py` (per-run 生命周期注入点)
- `main.py` (SDK per-run 生命周期注入点)
- `tests/utils/test_log_manager.py`
- `tests/utils/test_log_interface.py`
- `tests/server/test_request_logging.py`

## 核心流程

1. 调用方调用 `LogManager.init` 并传入目录、级别、轮转配置（`RotationConfig`）、敏感模式和日志保留天数。
2. `LogManager` 校验参数和日志目录安全性。
3. 初始化 common、metrics、interface logger (common 和 metrics 均创建第一个 per-run 文件)。init 时共享同一 `date_str` / `run_prefix`，确保 common 与 metrics 文件前缀一致。
4. 如果 `node_debug_enable=True`，额外初始化节点 debug logger。
5. 设置第三方 logger 级别，并记录当前敏感模式、日志目录和日志参数。
6. SDK 和 Server 每次运行时创建 per-run handler: `_generate_run_prefix` 生成共享前缀后，common handler 追加到 root logger，metrics handler 追加到 metrics logger，均带 `RunIdFilter` 按 run_id 隔离，运行结束后 `end_run` 清理。映射日志通过 `openjiuwen_deepsearch.log_manager` logger 记录，确保通过 `ProjectLoggerFilter`。
7. 业务入口通过 `session_id_ctx` 给通用日志添加 session 维度。
8. init handler 和 per-run handler 的构建逻辑分别由 `_create_common_file_handlers` / `_create_metrics_file_handler` 统一封装，消除重复代码。

## 数据契约与依赖

- `level` 只能是 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。
- `RotationConfig.max_bytes` 范围为 0 到 1000 MiB。
- `RotationConfig.backup_count` 范围为 0 到 1000。
- `RotationConfig`（`log_common.py`）封装 `max_bytes` 和 `backup_count`，由 `LogManager.init` 直接接收并在内部传给 `setup_common_logger` / `setup_metrics_logger` / `create_per_run_handler` / `create_per_run_metrics_handler`。
- `date_str` 和 `run_prefix` 由 `LogManager.init` / `new_run` 调用 `_generate_run_prefix` 一次性生成并封装为 `RunPrefix`（`log_common.py`），传给 common 和 metrics handler，确保同次运行文件前缀一致。
- interface 日志格式为 `role | session_id | api_name | duration_min | result | response_info_json`。
- metrics 日志使用 `TIME_LOGGER_TAG` 标记耗时打点。
- `log_retention_days` 作为 `LogManager.init(log_retention_days=...)` 参数控制日志保留天数（默认 30），`init` 和 `new_run` 时自动清理超过此天数的 `common/` 和 `metrics/` 日期文件夹；设为 0 则不清理。

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
