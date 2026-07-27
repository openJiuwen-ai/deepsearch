# 遥测事件服务

## 维护范围

本文档覆盖独立的 `server/telemetry_event_server.py` FastAPI 应用，包括遥测事件写入、JSONL 查询、后台 search/react run 启动和取消。

不覆盖主后端 `server.main` 的 API，也不覆盖 telemetry 事件的生产方实现。

## 功能目的

遥测事件服务为调试和实验场景提供轻量后台服务：接收运行时事件写入 JSONL，按 run_id 查询事件，并可启动带遥测上下文的 DeepSearch graph 运行。

## 可见行为

- 默认监听应用暴露 `/` 和 `/health` 作为纯文本健康检查。
- `POST /events` 接收 telemetry envelope，默认追加到 `output/telemetry_logs/telemetry.jsonl`。
- `POST /runs` 启动后台 `search` 或 `react` 工作流并立即返回 run id。
- `POST /runs/{run_id}/cancel` 尝试取消后台任务。
- `GET /telemetry/recent` 返回最近 N 条事件，可按 run_id 过滤。
- `GET /telemetry/range` 返回指定 run_id 和 seq 间隔内的事件。

## 关键代码路径

- `server/telemetry_event_server.py`
- `openjiuwen_deepsearch/utils/run_telemetry.py`
- `main.py`
- `tests/server/test_telemetry_event_server.py`

## 核心流程

1. CLI 解析 host、port、event path、JSONL 路径和 public base URL。
2. `_make_app` 根据全局运行状态构建 FastAPI app。
3. `POST /events` 校验 JSON body 和 telemetry envelope，写入 JSONL。
4. `POST /runs` 将请求转换为 `AgentConfig` 字典，创建 cancel event 和后台任务。
5. 后台任务在 `run_telemetry_session` 中调用 `main.py` 中的 `run_jiuwen_workflow`。
6. 任务完成、失败或取消时 emit lifecycle 事件。
7. app shutdown 会取消仍在运行的后台任务。

## 数据契约与依赖

- telemetry envelope 至少需要非空字符串 `event`，`payload` 如果存在必须是对象。
- `/runs` 只支持 `search_mode=search` 或 `react`，不支持报告研究 `research`。
- `tool_map=search_fetch` 时必须提供 `web_fetch_provider_config` 和 `web_search_engine_config`。
- `tool_map=retrieve` 时必须提供 Milvus embedder key 和 base URL。
- `conversation_id` 是 API 关联 ID；workflow 内部会生成自己的 conversation id。
- `MAX_RECENT_N` 当前为 10000。

## 边界与错误处理

- 非 JSON 或非对象事件请求返回 400。
- 开启 JSONL 持久化时，缺少有效 `event` 的请求返回 422。
- 重复启动仍在运行的 run_id 返回 409。
- 取消未知或已完成 run_id 返回 404。
- `start_seq > end_seq` 返回 422。
- 后台运行会临时设置 LLM/TOOL SSL 环境默认值，并在 finally 中恢复。
- `search_fetch` 模式下缺少任一 provider 配置对象时，请求会在 schema 校验阶段返回 422。

## 测试与验证

- `uv run pytest tests/server/test_telemetry_event_server.py`
- 手工启动：`uv run python -m server.telemetry_event_server`

## 相关文档

- [流式输出与运行遥测](../utils/streaming-and-telemetry.md)
- [DeepSearch 搜索工作流配置](../config/search-workflow-config.md)
- [DeepSearch 网页抓取 Provider 注册](../framework/web-fetch-provider-registry.md)
- [DeepSearch 搜索智能体](../algorithm/search-agent.md)
