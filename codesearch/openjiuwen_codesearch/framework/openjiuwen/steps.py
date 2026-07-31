# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent 循环的阶段函数：react 编排器与 workflow 图节点共享同一份逻辑。

- `reasoning_step`：一轮 LLM 决策（含 fail-fast、轮次上限、token/轨迹记账）；
- `tool_step`：执行本轮 pending 工具调用（含提交、停滞、临界警告）；
- `finalize`：按终止原因构造最终结果。

返回值约定：阶段函数返回 `Termination | None`；None 表示继续循环。
"""

import logging
from typing import Optional

from openjiuwen_codesearch.algorithm.memory_ops import construct_final_hits
from openjiuwen_codesearch.algorithm.reasoning import (
    TURN_LIMIT_WARNING,
    build_base_prompt,
    run_reasoning_turn,
)
from openjiuwen_codesearch.algorithm.search_tools.registry import (
    ToolSpec,
    build_default_registry,
    registry_schemas,
)
from openjiuwen_codesearch.domain.result import CodeSearchResult, Termination
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import CodeSearchRunContext
from openjiuwen_codesearch.llm.factory import ChatMessage

logger = logging.getLogger(__name__)

_REGISTRY: Optional[dict[str, ToolSpec]] = None


def get_registry() -> dict[str, ToolSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    return _REGISTRY


async def reasoning_step(ctx: CodeSearchRunContext) -> Optional[Termination]:
    agent_cfg = ctx.config.agent

    # fail-fast：索引未就绪不进循环（旧实现会空搜整整 max_turns 轮）
    if ctx.turn == 0 and not await ctx.retriever.has_revision(ctx.revision):
        return Termination.INDEX_NOT_READY
    if ctx.turn >= agent_cfg.max_turns:
        return Termination.MAX_TURNS

    ctx.turn += 1
    logger.info("Turn %d/%d...", ctx.turn, agent_cfg.max_turns)
    if not ctx.base_prompt:
        ctx.base_prompt = build_base_prompt(ctx.query, ctx.top_k)

    memory_text = ctx.memory.render()
    try:
        response = await run_reasoning_turn(
            ctx.main_llm, ctx.base_prompt, memory_text, ctx.history,
            registry_schemas(get_registry()),
        )
    except Exception as e:  # LLM 失败走降级出口
        logger.error("LLM API call failed: %s. Breaking loop.", e)
        ctx.error = str(e)
        return Termination.LLM_ERROR

    ctx.add_tokens("main_llm", response.input_tokens, response.output_tokens)
    ctx.write_trace(
        {
            "turn": ctx.turn,
            "query": ctx.query,
            "memory": memory_text,
            "completion": {
                "content": response.content,
                "tool_calls": [c.model_dump() for c in response.tool_calls],
            },
        }
    )
    ctx.history.append(
        ChatMessage(role="assistant", content=response.content or "", raw=response.raw)
    )

    if not response.tool_calls:
        logger.warning("LLM stopped tool calling early. Breaking loop.")
        return Termination.NO_TOOL_CALL

    ctx.pending_calls = list(response.tool_calls)
    return None


async def tool_step(ctx: CodeSearchRunContext) -> Optional[Termination]:
    agent_cfg = ctx.config.agent
    registry = get_registry()
    calls, ctx.pending_calls = ctx.pending_calls, []

    searched_this_turn = False
    added_this_turn = 0
    for call in calls:
        spec = registry.get(call.name)
        if spec is None:
            ctx.history.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=call.call_id,
                    content=f"Error: unknown tool '{call.name}'.",
                )
            )
            continue
        try:
            outcome = await spec.executor(ctx, call.arguments)
        except Exception as e:  # 单工具失败不终止循环
            logger.error("Tool '%s' failed: %s", call.name, e)
            ctx.history.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=call.call_id,
                    content=f"Error executing {call.name}: {e}",
                )
            )
            continue

        ctx.add_tokens("filter_llm", *outcome.filter_tokens)

        if outcome.submitted_ids is not None:
            ctx.submitted_ids = outcome.submitted_ids
            return Termination.SUBMITTED

        searched_this_turn = searched_this_turn or outcome.searched
        added_this_turn += outcome.added_snippets
        ctx.history.append(
            ChatMessage(role="tool", tool_call_id=call.call_id, content=outcome.message)
        )

    # 停滞终止：只统计"发生了检索却零新增"的轮次
    if searched_this_turn:
        if added_this_turn == 0:
            ctx.empty_search_rounds += 1
        else:
            ctx.empty_search_rounds = 0
        if ctx.empty_search_rounds >= agent_cfg.stagnation_rounds:
            logger.warning(
                "No new snippets for %d consecutive search rounds. Stopping early.",
                ctx.empty_search_rounds,
            )
            return Termination.STAGNATED

    # 轮次临界警告（与旧实现一致：最后 warn_before_turns 轮追加）
    if ctx.turn > agent_cfg.max_turns - agent_cfg.warn_before_turns:
        ctx.history.append(ChatMessage(role="user", content=TURN_LIMIT_WARNING))
    return None


def finalize(ctx: CodeSearchRunContext, termination: Termination) -> CodeSearchResult:
    ctx.termination = termination
    if termination == Termination.SUBMITTED:
        hits = construct_final_hits(ctx.submitted_ids[: ctx.top_k], ctx.memory)
    elif termination == Termination.INDEX_NOT_READY:
        hits = []
    else:
        logger.warning("Agentic loop ended (%s). Returning snippets from memory.", termination)
        hits = construct_final_hits(ctx.memory.ranked_saved_ids()[: ctx.top_k], ctx.memory)
    logger.info(
        "Token usage for this issue: input=%d output=%d",
        ctx.total_input_tokens,
        ctx.total_output_tokens,
    )
    result = CodeSearchResult(
        hits=hits,
        termination=termination,
        turns=ctx.turn,
        total_input_tokens=ctx.total_input_tokens,
        total_output_tokens=ctx.total_output_tokens,
        error=ctx.error,
    )
    ctx.result = result
    return result
