# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from enum import Enum

from pydantic import BaseModel, Field


class Termination(str, Enum):
    """运行终止原因。路由与结果出口一律使用本枚举，不用业务字符串。"""

    SUBMITTED = "submitted"  # agent 主动提交
    MAX_TURNS = "max_turns"  # 轮次耗尽，降级返回记忆内容
    NO_TOOL_CALL = "no_tool_call"  # LLM 停止调用工具，降级返回
    STAGNATED = "stagnated"  # 连续 N 轮检索无新 snippet，提前终止
    LLM_ERROR = "llm_error"  # LLM 调用异常，降级返回
    INDEX_NOT_READY = "index_not_ready"  # fail-fast：索引缺失/该 revision 无数据


class FinalHit(BaseModel):
    """最终结果中的一个行区间片段（每个不相交区间独立成条）。"""

    id: int
    file_path: str
    start_line: int
    end_line: int
    text: str
    kind: str = ""
    original_name: str = ""


class CodeSearchResult(BaseModel):
    hits: list[FinalHit] = Field(default_factory=list)
    termination: Termination
    turns: int = 0
    # 用量以 token 计（不含金额：单价随端点与时间变动，由调用方自行折算）
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str = ""


class CodeResolveResult(BaseModel):
    patch: str = ""
    termination: Termination
    turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str = ""
