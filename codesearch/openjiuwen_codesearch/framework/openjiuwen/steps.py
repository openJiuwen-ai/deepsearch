# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent 循环的阶段函数：react 编排器与 workflow 图节点共享同一份逻辑。

- `reasoning_step`：一轮 LLM 决策（含 fail-fast、轮次上限、token/轨迹记账）；
- `tool_step`：执行本轮 pending 工具调用（含提交、停滞、临界警告）；
- `finalize`：按终止原因构造最终结果。

返回值约定：阶段函数返回 `Termination | None`；None 表示继续循环。
"""

import json
import logging
from typing import Any, Optional

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
from openjiuwen_codesearch.domain.result import CodeSearchResult, CodeResolveResult, Termination
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import (
    CodeSearchRunContext,
    CodeResolveRunContext,
)
from openjiuwen_codesearch.llm.factory import ChatMessage

logger = logging.getLogger(__name__)

_REGISTRY: Optional[dict[str, ToolSpec]] = None


def get_registry() -> dict[str, ToolSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    return _REGISTRY


def _format_tool_args(arguments: Any) -> str:
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False, default=str)
    except TypeError:
        return repr(arguments)


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
        ctx.base_prompt = build_base_prompt(
            ctx.query,
            ctx.top_k,
            agent_cfg.max_turns,
            getattr(ctx, "issue_text", None),
            getattr(ctx, "past_queries", None),
        )

    persistent_str = ctx.memory.render("PERSISTENT MEMORY (Past Queries)")
    working_str = ctx.working_memory.render("WORKING MEMORY (Current Search)")
    memory_text = persistent_str + "\n" + working_str
    try:
        response = await run_reasoning_turn(
            ctx.main_llm,
            ctx.base_prompt,
            memory_text,
            ctx.history,
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
    for call in ctx.pending_calls:
        logger.info(
            "   🛠️  Tool [%d/%d turns]: %s args=%s",
            ctx.turn,
            agent_cfg.max_turns,
            call.name,
            _format_tool_args(call.arguments),
        )
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
        logger.warning(
            "Agentic loop ended (%s). Returning fallback snippets from working memory.", termination
        )
        fallback_ids = list(ctx.working_memory.saved.keys())[: ctx.top_k]
        for sid in fallback_ids:
            ranges = ctx.working_memory.saved[sid]
            ctx.memory.add_ranges(ctx.working_memory.cache[sid], ranges)

        if hasattr(ctx, "past_queries"):
            recorded_query = (
                ctx.query if getattr(ctx, "issue_text", None) else "Initial Issue Search"
            )
            ctx.past_queries.append(
                f"Query: '{recorded_query}' -> Submitted Snippets (Fallback): {fallback_ids}"
            )

        hits = construct_final_hits(fallback_ids, ctx.memory)
    logger.info(
        "Token usage for this issue: input=%d output=%d",
        ctx.total_input_tokens,
        ctx.total_output_tokens,
    )
    if ctx.result is None:
        ctx.result = CodeSearchResult(hits=hits, termination=termination)
    else:
        ctx.result.hits = hits
    ctx.result.turns = ctx.turn
    ctx.result.total_input_tokens = ctx.total_input_tokens
    ctx.result.total_output_tokens = ctx.total_output_tokens
    ctx.result.error = ctx.error
    return ctx.result


_RESOLVE_REGISTRY: Optional[dict[str, "ResolveToolSpec"]] = None


def get_resolve_registry() -> dict[str, "ResolveToolSpec"]:
    global _RESOLVE_REGISTRY
    if _RESOLVE_REGISTRY is None:
        from openjiuwen_codesearch.algorithm.coder_tools.registry import build_default_registry

        _RESOLVE_REGISTRY = build_default_registry()
    return _RESOLVE_REGISTRY


async def resolver_reasoning_step(ctx: CodeResolveRunContext) -> Optional[Termination]:
    agent_cfg = ctx.config.agent

    if ctx.turn >= agent_cfg.max_turns:
        return Termination.MAX_TURNS

    ctx.turn += 1
    logger.info("Resolver Turn %d/%d...", ctx.turn, agent_cfg.max_turns)

    if not ctx.base_prompt:
        # Load code_resolve.md and prepare initial context
        with open(
            "openjiuwen_codesearch/algorithm/prompts/code_resolve.md", "r", encoding="utf-8"
        ) as f:
            system_prompt = f.read()

        # We need the initial snippets for the prompt.
        result = await ctx.retriever.search(
            query=ctx.query,
            revision=ctx.commit,
            top_k=ctx.config.agent.search_topk,
        )
        initial_snippets = result.hits
        context_str = "--- INITIAL RETRIEVED CONTEXT ---\n"
        for snip in initial_snippets:
            fp = (
                snip.get("file_path", "unknown")
                if isinstance(snip, dict)
                else getattr(snip, "file_path", "unknown")
            )
            st = (
                snip.get("start_line", "?")
                if isinstance(snip, dict)
                else getattr(snip, "start_line", "?")
            )
            en = (
                snip.get("end_line", "?")
                if isinstance(snip, dict)
                else getattr(snip, "end_line", "?")
            )
            text = snip.get("text", "") if isinstance(snip, dict) else getattr(snip, "text", "")
            context_str += f"\nFile: {fp} (lines {st}-{en})\n```python\n{text}\n```\n"

        ctx.base_prompt = system_prompt + "\n\nIssue:\n" + ctx.query + "\n\n" + context_str

    try:
        from openjiuwen_codesearch.algorithm.coder_tools.registry import registry_schemas

        response = await run_reasoning_turn(
            ctx.main_llm,
            ctx.base_prompt,
            "",
            ctx.history,
            registry_schemas(get_resolve_registry()),
        )
    except Exception as e:
        logger.error("Resolver LLM API call failed: %s. Breaking loop.", e)
        ctx.error = str(e)
        return Termination.LLM_ERROR

    ctx.add_tokens("main_llm", response.input_tokens, response.output_tokens)
    ctx.write_trace(
        {
            "turn": ctx.turn,
            "prompt_length": len(ctx.base_prompt) + sum(len(m.content) for m in ctx.history),
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
        logger.warning("Resolver stopped tool calling early.")
        ctx.history.append(
            ChatMessage(
                role="user",
                content="You did not call any tools. If you are finished, you MUST call `submit_patch`. Otherwise, call a tool to continue.",
            )
        )
        return None

    for call in response.tool_calls:
        func_name = getattr(
            call, "name", getattr(getattr(call, "function", None), "name", "unknown")
        )
        logger.info("🚀 [RESOLVER] Decided to call tool: %s", func_name)

    ctx.pending_calls = list(response.tool_calls)
    return None


async def resolver_tool_step(ctx: CodeResolveRunContext) -> Optional[Termination]:
    agent_cfg = ctx.config.agent
    registry = get_resolve_registry()
    calls, ctx.pending_calls = ctx.pending_calls, []

    is_finished = False
    for call in calls:
        function_name = getattr(
            call, "name", getattr(getattr(call, "function", None), "name", None)
        )
        args_str = getattr(
            call, "arguments", getattr(getattr(call, "function", None), "arguments", "{}")
        )
        call_id = getattr(call, "call_id", getattr(call, "id", f"call_{ctx.turn}"))
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
            spec = registry.get(function_name)
            logger.info("🛠️ [RESOLVER TOOL] Executing: %s | Args: %s", function_name, args_str)
            if not spec:
                result = f"Error: Unknown function {function_name}"
            else:
                outcome = await spec.executor(ctx, args)
                if outcome.error:
                    result = outcome.error
                else:
                    result = outcome.message
                if outcome.patch_submitted:
                    is_finished = True

            log_result = result[:200] + "..." if len(result) > 200 else result
            logger.info("✅ [RESOLVER TOOL] %s returned: %s", function_name, log_result)
            ctx.history.append(ChatMessage(role="tool", content=result, tool_call_id=call_id))
            ctx.write_trace({
                "turn": ctx.turn,
                "tool_execution": {
                    "name": function_name,
                    "arguments": args_str,
                    "result": result
                }
            })
        except Exception as e:
            logger.error("Tool execution failed: %s", e)
            ctx.history.append(
                ChatMessage(
                    role="tool",
                    content=f"Error parsing or executing tool: {e}",
                    tool_call_id=call_id,
                )
            )

    if is_finished:
        return Termination.SUBMITTED

    if ctx.turn == agent_cfg.max_turns - 2:
        ctx.history.append(
            ChatMessage(
                role="user",
                content="SYSTEM WARNING: You have 1 turn left. You MUST finish your changes and call submit_patch.",
            )
        )

    return None


def finalize_resolve(ctx: CodeResolveRunContext, termination: Termination) -> CodeResolveResult:
    import subprocess
    import logging

    git_logger = logging.getLogger(__name__)

    final_diff = ""
    try:
        subprocess.run(["git", "add", "-A"], cwd=ctx.repo_dir, check=True)
        result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=ctx.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        final_diff = result.stdout
        
        # Unstage everything we just staged so we don't pollute the user's git index
        subprocess.run(["git", "reset"], cwd=ctx.repo_dir, capture_output=True, check=False)
    except Exception as e:
        git_logger.error(f"Failed to get diff: {e}")

    ctx.result = CodeResolveResult(
        patch=final_diff,
        termination=termination,
        turns=ctx.turn,
        total_input_tokens=ctx.input_tokens,
        total_output_tokens=ctx.output_tokens,
        error=ctx.error,
    )
    return ctx.result
