# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Protocol

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """一个按语法边界切出的代码块（1-based 闭区间行号）。"""

    text: str
    start_line: int
    end_line: int
    kind: str = ""
    name: str = ""
    calls: list[str] = Field(default_factory=list)


class Chunker(Protocol):
    """语言切块器协议。新增语言 = 新增实现文件。"""

    def chunk_source(self, source: str) -> list[Chunk]: ...

    def chunk_file(self, file_path: str) -> list[Chunk]: ...
