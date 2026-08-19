# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging

from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec

logger = logging.getLogger(__name__)

DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_snippets",
        "description": (
            "Delete irrelevant snippet IDs from your memory. Provide a reasoning for "
            "why they are irrelevant to guide your future searches."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "snippet_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of the snippet IDs to delete from memory.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Explanation of why these snippets are not useful, which will help you plan your next move.",
                },
            },
            "required": ["snippet_ids", "reasoning"],
        },
    },
}

SUBMIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_final_snippets",
        "description": (
            "Submit the final list of snippet IDs that are most relevant to solving "
            "the issue. This concludes the search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "snippet_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of the snippet IDs to keep. Order them by relevance (most relevant first).",
                }
            },
            "required": ["snippet_ids"],
        },
    },
}


async def execute_delete(env, args: dict) -> ToolOutcome:
    snippet_ids = args.get("snippet_ids", [])
    reasoning = args.get("reasoning", "")
    deleted = env.working_memory.delete(snippet_ids)
    logger.info("Agent deleted %d snippets. Reasoning: %s", deleted, reasoning)
    logger.info(
        "   🗑️  Agent delete_snippets [%d/%d turns]: deleted=%d ids=%s reasoning=%s",
        env.turn,
        env.config.agent.max_turns,
        deleted,
        snippet_ids,
        reasoning,
    )
    message = (
        f"Successfully deleted {deleted} snippets from your CURRENT SAVED SNIPPETS memory."
        f"\nYour Reasoning: {reasoning}"
    )
    return ToolOutcome(message=message)


async def execute_submit(env, args: dict) -> ToolOutcome:
    snippet_ids = [sid for sid in args.get("snippet_ids", []) if isinstance(sid, int)]
    logger.info("   ✅ Agent submitted %d final snippets.", len(snippet_ids))
    
    # Merge submitted snippets from working memory into persistent memory
    for sid in snippet_ids[:env.search_topk]:
        if sid in env.working_memory.saved:
            ranges = env.working_memory.saved[sid]
            env.memory.add_ranges(env.working_memory.cache[sid], ranges)

    if hasattr(env, "past_queries"):
        recorded_query = env.query if getattr(env, "issue_text", None) else "Initial Issue Search"
        env.past_queries.append(f"Query: '{recorded_query}' -> Submitted Snippets: {snippet_ids[:env.search_topk]}")

    return ToolOutcome(submitted_ids=snippet_ids[: env.search_topk])


DELETE_SPEC = ToolSpec(name="delete_snippets", schema=DELETE_SCHEMA, executor=execute_delete)
SUBMIT_SPEC = ToolSpec(name="submit_final_snippets", schema=SUBMIT_SCHEMA, executor=execute_submit)
