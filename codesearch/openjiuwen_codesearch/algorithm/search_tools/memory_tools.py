# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging

from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec

logger = logging.getLogger(__name__)

DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_snippets",
        "description": "Delete irrelevant snippet IDs from your memory. Provide a reasoning for why they are irrelevant to guide your future searches.",
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
        "description": "Submit the final list of snippet IDs that are most relevant to solving the issue. This concludes the search.",
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
    deleted = env.memory.delete(snippet_ids)
    logger.info("Agent deleted %d snippets. Reasoning: %s", deleted, reasoning)
    message = (
        f"Successfully deleted {deleted} snippets from your CURRENT SAVED SNIPPETS memory."
        f"\nYour Reasoning: {reasoning}"
    )
    return ToolOutcome(message=message)


async def execute_submit(env, args: dict) -> ToolOutcome:
    snippet_ids = [sid for sid in args.get("snippet_ids", []) if isinstance(sid, int)]
    logger.info("Agent submitted %d final snippets.", len(snippet_ids))
    return ToolOutcome(submitted_ids=snippet_ids)


DELETE_SPEC = ToolSpec(name="delete_snippets", schema=DELETE_SCHEMA, executor=execute_delete)
SUBMIT_SPEC = ToolSpec(name="submit_final_snippets", schema=SUBMIT_SCHEMA, executor=execute_submit)
