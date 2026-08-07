# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM 通用数据类型：会话消息、工具调用、规范化响应。"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    """规范化后的 LLM 工具调用。arguments 已解析为 dict。"""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""


class ChatMessage(BaseModel):
    """规范化会话消息。assistant 消息可携带 provider 原生对象（raw）以保持历史保真。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: str  # user | assistant | tool
    content: str = ""
    tool_call_id: str = ""
    raw: Any = None


class LLMResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: Optional[str] = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None
