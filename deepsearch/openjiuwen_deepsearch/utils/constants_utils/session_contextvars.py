# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import contextvars

session_context = contextvars.ContextVar("session")
model_context = contextvars.ContextVar("model_context")
llm_context = contextvars.ContextVar("llm")
web_search_context = contextvars.ContextVar("web_search")
local_search_context = contextvars.ContextVar("local_search")
cancel_context = contextvars.ContextVar("cancel_event", default=None)
# Holds the per-run tool registry (e.g. WebSearch/WebFetch/Retrieve instances).
# Stored in a ContextVar instead of being passed through workflow inputs because
# the framework's structured logger calls ``dataclasses.asdict`` on log events,
# which deep-copies the inputs; tool clients (e.g. MilvusClient) hold
# ``_thread.RLock`` objects that cannot be deep-copied.
tool_context = contextvars.ContextVar("tool_map", default=None)
# 禁引约束总开关（对应 AgentConfig.exclusion_constraint_enable）。
# 意图识别内部需要它，但 `_emit_report_intent` 是 LLM tool_call 的 handler，
# 入参完全来自模型输出，无法再挂额外上下文，故与 tool_context 同样用 ContextVar 传递。
# 默认 False：未显式开启时禁引增强不生效（baseline 行为）。
exclusion_constraint_context = contextvars.ContextVar("exclusion_constraint_enable", default=False)
