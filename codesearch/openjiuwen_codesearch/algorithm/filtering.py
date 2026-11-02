# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""过滤 agent：对检索到的 chunk 逐行提取与 issue 相关的行区间。

具名内部阶段：独立函数、独立计时与 token 归因，
但不是 workflow 节点；并发以 semaphore 封顶。
行号标注逻辑与旧 `filter_chunk` 一致。
"""

import asyncio
import logging

from openjiuwen_codesearch.algorithm.prompts import load_prompt
from openjiuwen_codesearch.domain.models import Snippet
from openjiuwen_codesearch.llm.factory import ChatMessage, LLMClient

logger = logging.getLogger(__name__)

# (start_line, end_line) 闭区间；(input_tokens, output_tokens) 用量
LineRangeTuple = tuple[int, int]
TokenUsage = tuple[int, int]

FILTER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_relevant_lines",
        "description": "Extract lines from the code snippet that are relevant to solving the issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "selections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["start_line", "end_line", "reasoning"],
                    },
                }
            },
            "required": ["selections"],
        },
    },
}


def number_snippet_lines(snippet: Snippet) -> str:
    """为 chunk 正文标注源文件行号；头两行 `File:` 头与空行不标注。"""
    lines = snippet.text.split("\n")
    numbered = []
    for idx, line in enumerate(lines):
        if idx < 2 and line.startswith("File:"):
            numbered.append(line)
        elif line.strip() == "":
            numbered.append(line)
        else:
            numbered.append(f"{snippet.start_line + max(0, idx - 2)}: {line}")
    return "\n".join(numbered)


async def filter_snippet(
    llm: LLMClient, query: str, snippet: Snippet
) -> tuple[list[LineRangeTuple], TokenUsage]:
    """单个 chunk 的相关行提取。返回 (行区间, 本次调用的 token 用量)。
    异常返回空区间与零用量，失败即放弃该 chunk。
    """
    prompt = load_prompt("filter_chunk").format(
        query=query, snippet=number_snippet_lines(snippet)
    )
    try:
        response = await llm.invoke(
            [ChatMessage(role="user", content=prompt)], tools=[FILTER_TOOL_SCHEMA]
        )
        ranges: list[tuple[int, int]] = []
        for call in response.tool_calls:
            if call.name != "save_relevant_lines":
                continue
            for sel in call.arguments.get("selections", []):
                st, en = sel.get("start_line"), sel.get("end_line")
                if isinstance(st, int) and isinstance(en, int):
                    ranges.append((st, en))
        return ranges, (response.input_tokens, response.output_tokens)
    except Exception as e:  # 过滤失败不致命，丢弃该 chunk
        logger.error("Filter agent failed: %s", e)
        return [], (0, 0)


async def filter_snippets(
    llm: LLMClient,
    query: str,
    snippets: list[Snippet],
    concurrency: int,
) -> list[tuple[Snippet, list[LineRangeTuple], TokenUsage]]:
    """有界并发过滤。返回 [(snippet, ranges, (in_tokens, out_tokens))]，顺序与输入一致。"""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(snippet: Snippet) -> tuple[Snippet, list[LineRangeTuple], TokenUsage]:
        async with semaphore:
            ranges, usage = await filter_snippet(llm, query, snippet)
            return snippet, ranges, usage

    return list(await asyncio.gather(*[_one(s) for s in snippets]))
