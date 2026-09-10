# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""共享测试设施：fake LLM / snippet 构造 / 事件循环辅助。

全部单测不依赖 pytest-asyncio：async 逻辑用 `run()`（asyncio.run 包装）驱动。
"""

import asyncio
from typing import Callable, Optional

import pytest

from openjiuwen_codesearch.domain.models import Snippet, ToolCall
from openjiuwen_codesearch.llm.factory import ChatMessage, LLMResponse


def run(coro):
    return asyncio.run(coro)


def make_snippet(
    sid: int,
    file_path: str,
    start_line: int,
    body_lines: list[str],
    **opts,
) -> Snippet:
    with_header = opts.get("with_header", True)
    kind = opts.get("kind", "function_definition")
    name = opts.get("name", "")
    end_line = start_line + len(body_lines) - 1
    if with_header:
        text = f"File: {file_path} (L{start_line}-L{end_line})\n\n" + "\n".join(body_lines)
    else:
        text = "\n".join(body_lines)
    return Snippet(
        id=sid,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        text=text,
        kind=kind,
        original_name=name,
    )


def tool_call_response(
    calls: list[tuple[str, dict]], content: str = "", tokens: tuple[int, int] = (0, 0)
) -> LLMResponse:
    return LLMResponse(
        content=content,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        tool_calls=[
            ToolCall(name=n, arguments=a, call_id=f"call_{i}") for i, (n, a) in enumerate(calls)
        ],
    )


class FakeLLM:
    """脚本化 LLM：按顺序吐出预置响应，或用 handler 按输入内容动态生成。"""

    def __init__(
        self,
        responses: Optional[list[LLMResponse]] = None,
        handler: Optional[Callable[[list[ChatMessage], Optional[list[dict]]], LLMResponse]] = None,
    ) -> None:
        self.responses = list(responses or [])
        self.handler = handler
        self.calls: list[tuple[list[ChatMessage], Optional[list[dict]]]] = []
        self.call_kwargs: list[dict] = []

    async def invoke(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict]] = None,
        **kwargs,
    ) -> LLMResponse:
        self.calls.append((messages, tools))
        self.call_kwargs.append(dict(kwargs))
        if self.handler is not None:
            return self.handler(messages, tools)
        if not self.responses:
            return LLMResponse(content="", tool_calls=[])
        return self.responses.pop(0)


def make_filter_llm(
    range_by_file: dict[str, tuple[int, int]], tokens: tuple[int, int] = (0, 0)
) -> FakeLLM:
    """过滤 agent 的 fake：按提示词中出现的文件路径返回预设行区间。"""

    def handler(messages, tools):
        prompt = messages[0].content
        for fp, (st, en) in range_by_file.items():
            if fp in prompt:
                return tool_call_response(
                    [(
                        "save_relevant_lines",
                        {"selections": [{"start_line": st, "end_line": en, "reasoning": "r"}]},
                    )],
                    tokens=tokens,
                )
        return tool_call_response([("save_relevant_lines", {"selections": []})], tokens=tokens)

    return FakeLLM(handler=handler)


@pytest.fixture(autouse=True)
def _no_trace(tmp_path, monkeypatch):
    """测试默认在 tmp 目录运行，避免 agent_logs / 缓存文件散落仓库。"""
    monkeypatch.chdir(tmp_path)
