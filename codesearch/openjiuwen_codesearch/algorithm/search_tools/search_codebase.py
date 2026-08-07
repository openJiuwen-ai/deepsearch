# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""search_codebase 工具：稀疏检索 + 过滤 agent + 记忆写入。

结果消息文本与旧实现逐字一致（parity 契约）。
`target_file` 沿用旧实现的前缀 hack（依赖索引侧注入的 `File:` 文本头参与 BM25），
改为真过滤条件属行为变更，需另行登记。
"""

import logging

from openjiuwen_codesearch.algorithm.filtering import filter_snippets
from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_codebase",
        "description": (
            "Searches the codebase for relevant code snippets. You can call this "
            "multiple times to try different keywords."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {
                    "type": "string",
                    "description": (
                        "The optimized search keywords extracted from the problem "
                        "statement. DO NOT use regex."
                    ),
                },
                "use_trigram": {
                    "type": "boolean",
                    "description": (
                        "Set to True for Trigram BM25 (best for stack traces, partial "
                        "matches, or obfuscated names). Set to False for standard Token "
                        "BM25."
                    ),
                },
                "target_file": {
                    "type": "string",
                    "description": "Optional. The file path mentioned in the issue to prioritize.",
                },
            },
            "required": ["search_query", "use_trigram"],
        },
    },
}


async def execute(env, args: dict) -> ToolOutcome:
    search_query = args.get("search_query", env.query)
    use_trigram = bool(args.get("use_trigram", False))
    target_file = args.get("target_file")
    max_turns = env.config.agent.max_turns
    logger.info(
        "Agent search: query=%r trigram=%s file=%s", search_query, use_trigram, target_file
    )
    logger.info(
        "   🔍 Agent Search [%d/%d turns]: query='%s', trigram=%s, file=%s",
        env.turn,
        max_turns,
        search_query,
        use_trigram,
        target_file,
    )

    if target_file and "file:" not in search_query.lower():
        search_query = f"File: {target_file} " + search_query

    hits = await env.retriever.search(
        search_query, revision=env.revision, topk=env.search_topk, use_trigram=use_trigram
    )

    # 先登记本次检索的相关性证据（含已处理过的片段——被反复命中是强信号），
    # 供降级路径按相关性而非写入顺序兜底
    for rank, hit in enumerate(hits):
        env.memory.record_hit(hit, rank)

    unprocessed = [hit for hit in hits if not env.memory.is_processed(hit.id)]
    results = await filter_snippets(
        env.filter_llm, env.query, unprocessed, env.filter_concurrency
    )

    added = 0
    filter_in = filter_out = 0
    for snippet, ranges, usage in results:
        env.memory.mark_processed(snippet)
        filter_in += usage[0]
        filter_out += usage[1]
        if env.memory.add_ranges(snippet, ranges):
            added += 1

    if added == 0:
        if not unprocessed:
            message = (
                "Search completed, but ALL retrieved chunks were already processed in "
                "previous turns. Try entirely different keywords."
            )
        else:
            message = (
                "Search completed, but the Filter Agent found NO relevant lines in any "
                "of the retrieved snippets. Try different keywords."
            )
    else:
        message = (
            f"Search completed. {added} new snippets were filtered and added to your "
            "CURRENT SAVED SNIPPETS memory."
        )

    return ToolOutcome(
        message=message, added_snippets=added, searched=True,
        filter_tokens=(filter_in, filter_out)
    )


SPEC = ToolSpec(name="search_codebase", schema=SCHEMA, executor=execute)
