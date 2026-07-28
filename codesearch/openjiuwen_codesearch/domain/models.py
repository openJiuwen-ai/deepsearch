# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, Optional

from pydantic import BaseModel, Field


class LineRange(BaseModel):
    """闭区间行范围（1-based，两端含）。"""

    start: int
    end: int


class Snippet(BaseModel):
    """一个被检索到的代码块（对应旧实现中 Milvus hit 的裸 dict）。

    `text` 可能带两行头部：``File: {path} (L{s}-L{e})`` + 空行，由索引侧注入；
    记忆渲染与最终结果构造依赖该头部约定（`has_header`）。
    """

    id: int
    file_path: str
    start_line: int
    end_line: int
    text: str
    kind: str = ""
    original_name: str = ""
    score: Optional[float] = None

    @property
    def has_header(self) -> bool:
        lines = self.text.split("\n")
        return len(lines) >= 2 and lines[0].startswith("File:")

    @property
    def body_lines(self) -> list[str]:
        """去掉头部后的正文行；正文第 i 行对应源文件行号 start_line + i。"""
        lines = self.text.split("\n")
        return lines[2:] if self.has_header else lines

    @property
    def header_lines(self) -> list[str]:
        lines = self.text.split("\n")
        return lines[:2] if self.has_header else []


class ToolCall(BaseModel):
    """规范化后的 LLM 工具调用。arguments 已解析为 dict。"""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""
