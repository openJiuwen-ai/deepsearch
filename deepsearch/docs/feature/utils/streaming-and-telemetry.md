# 流式输出与运行遥测

## 维护范围

本文档覆盖 custom stream 事件封装和 run telemetry HTTP 上报能力。

不覆盖 server 端 telemetry 接收服务实现；该部分属于 `server/`。

## 功能目的

流式工具统一 workflow 节点向 openJiuwen session 写消息的协议；运行遥测工具为长任务提供可选、非阻塞的 HTTP 事件上报能力。

## 可见行为

- `custom_stream_output` 会按 `START -> MESSAGE -> DONE` 顺序写入三条 custom stream 事件。
- 每条 stream payload 包含 `message_id`、`agent`、`content`、`message_type`、`event`、`created_time`。
- 调用方可通过 `stream_meta` 追加 `section_idx`、`plan_idx`、`step_idx` 等字段。
- telemetry 只有进入 `run_telemetry_session` 并提供 URL 时才启用。
- `emit` 使用 daemon thread 发送 HTTP POST，不阻塞主流程。
- 敏感日志模式下，telemetry messages payload 只包含 count，不包含消息正文。

## 关键代码路径

- `openjiuwen_deepsearch/utils/common_utils/stream_utils.py`
- `openjiuwen_deepsearch/utils/run_telemetry.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `tests/server/test_telemetry_event_server.py`
- `tests/workflow/test_workflow_run.py`

## 核心流程

1. 节点调用 `custom_stream_output`，传入 session、消息 id、内容、agent 名称和可选元数据。
2. 工具构造 start/message/done payload，并依次调用 `session.write_custom_stream`。
3. telemetry 使用 `RunTelemetryConfig` 显式配置 URL、run_id、token、header 和 timeout。
4. `run_telemetry_session` 把配置放入 ContextVar。
5. `emit` 构造 envelope，追加 source、seq、timestamp、run_id、action_execution 等字段。
6. 后台线程将 JSON payload POST 到配置的 HTTP endpoint。

## 数据契约与依赖

- `MessageType` 当前包含 `message_chunk` 和 `interrupt`。
- `StreamEvent` 当前包含 `start`、`done`、`message`、`summary_response`、`waiting_user_input`、
  `user_input_ended`、`error`。
- telemetry payload 会被 `json_safe_for_telemetry` 递归转为 JSON 安全结构。
- bytearray/bytes 在 telemetry 中会被替换为 `***`。
- 非序列化对象会以 `<non-serializable module.Type>` 占位。

## 边界与错误处理

- telemetry URL 为空时，配置构造会抛 `ValueError`。
- telemetry 发送失败只记录 debug 日志，不影响主流程。
- telemetry 最大递归深度超过限制时返回 `<max depth exceeded>`。
- custom stream 输出本身不捕获 session 写入异常，调用节点需按自身边界处理。

## 测试与验证

- `uv run pytest tests/server/test_telemetry_event_server.py`
- 修改 workflow 流式输出契约时，补充运行 `uv run pytest tests/workflow/test_workflow_run.py`。

## 相关文档

- [报告研究主工作流](../framework/research-workflow.md)
- [节点基类与会话上下文](../framework/base-node-and-session-context.md)
