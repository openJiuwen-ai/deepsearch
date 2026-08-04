# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""expand_context 工具：按文件+行区间直接取索引内容并写入记忆。

请求区间按每个命中 chunk 的自身边界**裁剪**后再入记忆——不再把完整区间挂给所有重叠 chunk。
嵌套定义导致的 chunk 天然重叠仍可能带来少量重复，由最终结果的排序与去重语义兜底。
"""

import logging

from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "expand_context",
        "description": (
            "Fetch a specific line range from a specific file. Use this if a retrieved "
            "chunk is cut off. The fetched lines will be injected into memory as a new "
            "snippet that you can later save. Keep requests to ~50-200 lines to avoid "
            "context explosion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_file": {
                    "type": "string",
                    "description": "The exact file path (e.g. django/models/query.py)",
                },
                "start_line": {
                    "type": "integer",
                    "description": "The starting line number to fetch.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "The ending line number to fetch.",
                },
            },
            "required": ["target_file", "start_line", "end_line"],
        },
    },
}


async def execute(env, args: dict) -> ToolOutcome:
    target_file = args["target_file"]
    start_line = int(args["start_line"])
    end_line = int(args["end_line"])
    logger.info("Agent expanding context: %s lines %s-%s", target_file, start_line, end_line)

    chunks = await env.retriever.fetch_overlapping(
        env.revision, target_file, start_line, end_line
    )
    if not chunks:
        return ToolOutcome(message="No surrounding lines found in index.", searched=True)

    added = 0
    for chunk in chunks:
        clipped = (max(start_line, chunk.start_line), min(end_line, chunk.end_line))
        if clipped[0] > clipped[1]:
            continue
        # 智能体显式点名要的上下文：按最高优先级计入相关性证据
        env.memory.record_hit(chunk, rank=0)
        env.memory.mark_processed(chunk)
        if env.memory.add_ranges(chunk, [clipped]):
            added += 1

    message = (
        f"Expanded Context lines {start_line}-{end_line} for {target_file} "
        "have been added to your CURRENT SAVED SNIPPETS memory."
    )
    return ToolOutcome(message=message, added_snippets=added, searched=True)


SPEC = ToolSpec(name="expand_context", schema=SCHEMA, executor=execute)
