# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging

from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "view_repo_map",
        "description": "Returns a map of all files in the repository. Use this to discover file paths that you can then pass to the `target_file` argument in `search_codebase`.",
        "parameters": {"type": "object", "properties": {}},
    },
}


async def execute(env, args: dict) -> ToolOutcome:
    logger.info("Agent requested repo map")
    repo_map_str = await env.retriever.get_repo_map(env.revision)
    return ToolOutcome(message=repo_map_str)


SPEC = ToolSpec(name="view_repo_map", schema=SCHEMA, executor=execute)
