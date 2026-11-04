# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import contextvars

session_context = contextvars.ContextVar("session")
model_context = contextvars.ContextVar("model_context")
llm_context = contextvars.ContextVar("llm")
web_search_context = contextvars.ContextVar("web_search")
local_search_context = contextvars.ContextVar("local_search")
cancel_context = contextvars.ContextVar("cancel_event", default=None)
# 只保存运行期进度对象，不放入 workflow inputs 或 checkpoint。
report_progress_context = contextvars.ContextVar("report_progress", default=None)
# Holds the per-run tool registry (e.g. WebSearch/WebFetch/Retrieve instances).
# Stored in a ContextVar instead of being passed through workflow inputs because
# the framework's structured logger calls ``dataclasses.asdict`` on log events,
# which deep-copies the inputs; tool clients (e.g. MilvusClient) hold
# ``_thread.RLock`` objects that cannot be deep-copied.
tool_context = contextvars.ContextVar("tool_map", default=None)
# MCP tool bundle (持有持久连接，不可 deep-copy，故用 contextvar)
mcp_tool_context = contextvars.ContextVar("mcp_tool_bundle", default=None)
# 任务持有的网页正文缓存；不进入会被深拷贝或持久化的 workflow state。
webpage_fetch_cache_context = contextvars.ContextVar("webpage_fetch_cache", default=None)
