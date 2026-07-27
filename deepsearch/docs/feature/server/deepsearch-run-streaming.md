# DeepSearch 运行与 SSE 流

## 维护范围

本文档覆盖 `/api/v1/agent/deepsearch/run/` 的 DeepSearch 运行入口、SSE 输出、HITL 续跑、取消处理、本地任务状态和 Redis 跨进程取消。

不覆盖 Agent 配置组装细节；见相关文档。

## 功能目的

DeepSearch 运行接口把前端请求转为可流式消费的研究任务。它负责启动或继续 Agent 运行、透传流式 chunk、在等待用户输入时保留会话状态，并支持本进程和跨进程取消。

## 可见行为

- 普通请求返回 `text/event-stream`，流内容来自 `agent.run(...)`。
- `interrupt_feedback=cancel` 不启动新 SSE 流，而是触发取消逻辑并返回 JSON 状态。
- 取消成功可能返回 `cancelling`；本进程没有活动任务但 Redis 转发成功时返回 `forwarded`。
- Agent 输出 `waiting_user_input` 时，consumer 会结束当前响应但保留 cancel event，允许后续继续或取消。
- 已取消会话再次请求时会直接返回一次 `CANCELLED` 事件并清理本地 cancel 状态。
- 流结束且不是 HITL 等待时，会清理 agent 缓存和 checkpointer 会话状态。

## 关键代码路径

- `server/routers/deepsearch_run.py`
- `server/deepsearch/core/manager/agent.py`
- `server/core/cancel_bus.py`
- `server/core/runner_init.py`
- `server/schemas/deepsearch_run.py`
- `tests/server/test_deepsearch_run.py`
- `tests/server/test_agent_manager.py`

## 核心流程

1. Router 接收 `DeepSearchRequest`。
2. 如果 `interrupt_feedback=cancel`，进入 `_handle_cancel_request`。
3. 否则 `_prepare_stream_context` 规范化 LLM key、构建 agent_config、获取或创建 Agent，并按 `template_id` 加载模板。
4. `_create_streaming_response` 创建 queue、cancel event、resume event 和 producer task。
5. `_produce_stream` 调用 `_wrapped_agent_run`，把 Agent chunk 写入 queue。
6. SSE consumer 从 queue 读取 chunk 并 yield 给调用方。
7. consumer finally 根据是否等待用户输入决定保留或清理本地状态。

## 数据契约与依赖

- `DeepSearchRequest.conversation_id` 只能包含 ASCII 字母、数字、下划线和连字符，长度 1 到 128。
- `interrupt_feedback` 支持空值、`accepted`、`cancel`、`revise_outline`、`revise_comment`。
- 任务 key 为 `<space_id>:<conversation_id>`。
- 本地运行状态存放在 `_running_tasks`、`_cancel_events`、`_cancel_event_timestamps`、`_running_agents` 和 `_resume_requested_events`。
- cancel event 最大保留数量为 10，超出后按时间清理旧项。
- Redis 取消频道为 `deepsearch:cancel`。

## 边界与错误处理

- Web/local 搜索配置异常会分别映射为 HTTP 400。
- 模板不存在映射为 HTTP 404。
- 其他未分类异常映射为 HTTP 500。
- consumer 被取消时会取消 producer task，但不会把 HTTP 断连强行视为业务取消。
- 取消路径会尝试停止 openJiuwen controller 的 task queue 和 processing handler。
- HITL 等待时不清理 checkpointer，确保后续输入可恢复会话。

## 测试与验证

- `uv run pytest tests/server/test_deepsearch_run.py`
- `uv run pytest tests/server/test_agent_manager.py`
- 修改任务状态字典或 HITL 等待逻辑时，补充针对取消、恢复和清理路径的 server 单测。

## 相关文档

- [DeepSearch Agent 配置组装](./deepsearch-agent-config.md)
- [Server 应用运行时](./fastapi-app-runtime.md)
- [Agent 工厂与运行模式](../framework/agent-factory.md)
- [报告研究主工作流](../framework/research-workflow.md)
