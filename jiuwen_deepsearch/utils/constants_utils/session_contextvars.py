# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import contextvars

session_context = contextvars.ContextVar("session")
llm_context = contextvars.ContextVar("llm")
web_search_context = contextvars.ContextVar("web_search")
local_search_context = contextvars.ContextVar("local_search")
