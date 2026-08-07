# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure data models. This package must not import any other package module."""

from openjiuwen_codesearch.domain.models import LineRange, Snippet, ToolCall
from openjiuwen_codesearch.domain.memory import SnippetMemory, merge_intervals
from openjiuwen_codesearch.domain.result import CodeSearchResult, FinalHit, Termination

__all__ = [
    "LineRange",
    "Snippet",
    "ToolCall",
    "SnippetMemory",
    "merge_intervals",
    "CodeSearchResult",
    "FinalHit",
    "Termination",
]
