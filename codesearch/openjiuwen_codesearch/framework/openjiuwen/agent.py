# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeSearchAgent（react）与 RetropusCodeSearchAgent。

react 形态由 ``CodeSearchAgent.run`` 的 while 循环驱动，直接调用 steps.py 的
``reasoning_step`` / ``tool_step`` / ``finalize``，与图形态（workflow.py 的
``GraphCodeSearchAgent``）共享同一份阶段逻辑。无实例运行态（全部在 RunContext），
同一实例可安全并发 run。

``RetropusCodeSearchAgent`` 走 ``AbstractReactEngine`` 控制流，使用独立的
retropus registry，不触碰 CodeSearch 的 ``get_registry()``。
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Optional, TypeVar

from openjiuwen_codesearch.algorithm.search_tools.registry import registry_schemas
from openjiuwen_codesearch.domain.result import CodeSearchResult, FinalHit, Termination
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import (
    CodeSearchRunContext,
    CodeResolveRunContext,
    run_resolve_session,
)
from openjiuwen_codesearch.framework.openjiuwen.steps import finalize, reasoning_step, tool_step
from openjiuwen_codesearch.llm.factory import ChatMessage

if TYPE_CHECKING:
    from openjiuwen_codesearch.framework.openjiuwen.retropus_context import RetropusRunContext

logger = logging.getLogger(__name__)

TContext = TypeVar("TContext")


def _read_span_text(repo_dir: Path, file_path: str, start: int, end: int) -> str:
    try:
        full = repo_dir / file_path
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        # retropus spans are 1-indexed inclusive
        chunk = lines[max(0, start - 1): end]
        return "\n".join(chunk)
    except OSError:
        return ""


def spans_to_hits(spans: list[dict], repo_dir: Path, top_k: int) -> list[FinalHit]:
    hits: list[FinalHit] = []
    for i, span in enumerate(spans[:top_k]):
        file_path = str(span["file"])
        start = int(span["start"])
        end = int(span["end"])
        hits.append(
            FinalHit(
                id=i + 1,
                file_path=file_path,
                start_line=start,
                end_line=end,
                text=_read_span_text(repo_dir, file_path, start, end),
            )
        )
    hits.sort(key=lambda h: (h.file_path, h.start_line))
    # re-assign ids after sort for stable presentation
    for i, hit in enumerate(hits):
        hit.id = i + 1
    return hits


class AbstractReactEngine(ABC, Generic[TContext]):
    """Shared reason → tool → finalize control flow for react engines."""

    async def run(self, ctx: TContext) -> CodeSearchResult:
        while True:
            termination = await self.reasoning_step(ctx)
            if termination is not None:
                return self.finalize(ctx, termination)
            termination = await self.tool_step(ctx)
            if termination is not None:
                return self.finalize(ctx, termination)

    @abstractmethod
    async def reasoning_step(self, ctx: TContext) -> Optional[Termination]:
        ...

    @abstractmethod
    async def tool_step(self, ctx: TContext) -> Optional[Termination]:
        ...

    @abstractmethod
    def finalize(self, ctx: TContext, termination: Termination) -> CodeSearchResult:
        ...


class CodeSearchAgent:
    """react 形态：直接复用 steps.py 的阶段函数，与图形态输出逐字节一致。"""

    async def run(self, ctx: CodeSearchRunContext) -> CodeSearchResult:
        logger.info("Starting multi-turn agentic retrieval loop (react engine)...")
        while True:
            termination = await reasoning_step(ctx)
            if termination is not None:
                return finalize(ctx, termination)
            termination = await tool_step(ctx)
            if termination is not None:
                return finalize(ctx, termination)


class RetropusCodeSearchAgent(AbstractReactEngine["RetropusRunContext"]):
    """Retropus retrieval agent: KG/BM25 tools + openjiuwen LLM client."""

    async def run(self, ctx: "RetropusRunContext") -> CodeSearchResult:
        """Run the Retropus ReAct loop and return a ``CodeSearchResult``."""
        logger.info("Starting retropus retrieval loop...")
        return await super().run(ctx)

    def _ensure_history(self, ctx: "RetropusRunContext") -> None:
        """Seed system + issue user messages on first turn (idempotent)."""
        from openjiuwen_codesearch.algorithm.prompts.retropus import (  # noqa: PLC0415
            build_issue_user_message,
            build_system_prompt,
            stable_prompt_cache_key,
        )
        from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (  # noqa: PLC0415
            build_retropus_registry,
        )

        if ctx.history:
            return
        cfg = ctx.retropus_config
        ctx.system_prompt = build_system_prompt(
            inherits_expand=cfg.feat_inherits_expand,
            expand_imports=cfg.feat_expand_imports,
        )
        tool_schemas = registry_schemas(build_retropus_registry(ctx.tools))
        ctx.prompt_cache_key = stable_prompt_cache_key(ctx.system_prompt, tool_schemas)
        ctx.history = [
            ChatMessage(role="system", content=ctx.system_prompt),
            ChatMessage(role="user", content=build_issue_user_message(ctx.issue_text)),
        ]
        logger.info(
            "Retropus start: max_rounds=%d max_tool_calls=%d prompt_cache_key=%s",
            cfg.max_rounds,
            cfg.max_tool_calls,
            ctx.prompt_cache_key,
        )

    async def reasoning_step(self, ctx: "RetropusRunContext") -> Optional[Termination]:
        """Invoke the LLM for the next tool calls; nudge once if no spans yet."""
        from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (  # noqa: PLC0415
            build_retropus_registry,
        )
        from openjiuwen_codesearch.algorithm.prompts.retropus import (  # noqa: PLC0415
            NUDGE_NO_SPANS_PROMPT,
        )

        cfg = ctx.retropus_config
        self._ensure_history(ctx)

        if ctx.turn >= cfg.max_rounds or ctx.tool_calls_made >= cfg.max_tool_calls:
            return Termination.MAX_TURNS

        registry = build_retropus_registry(ctx.tools)
        tool_schemas = registry_schemas(registry)

        ctx.turn += 1
        invoke_kwargs: dict = {}
        if ctx.prompt_cache_key:
            invoke_kwargs["prompt_cache_key"] = ctx.prompt_cache_key
        try:
            response = await ctx.main_llm.invoke(ctx.history, tools=tool_schemas, **invoke_kwargs)
        except Exception as e:  # noqa: BLE001
            logger.error("Retropus LLM call failed: %s", e)
            ctx.error = str(e)
            return Termination.LLM_ERROR

        ctx.add_tokens("main_llm", response.input_tokens, response.output_tokens)
        ctx.write_trace(
            {
                "turn": ctx.turn,
                "query": ctx.query,
                "completion": {
                    "content": response.content,
                    "tool_calls": [c.model_dump() for c in response.tool_calls],
                },
            }
        )
        ctx.history.append(
            ChatMessage(
                role="assistant",
                content=response.content or "",
                raw=response.raw,
            )
        )

        if not response.tool_calls:
            if not ctx.tools.has_spans() and ctx.nudges < 2:
                ctx.nudges += 1
                ctx.history.append(ChatMessage(role="user", content=NUDGE_NO_SPANS_PROMPT))
                ctx.pending_calls = []
                return None
            return Termination.NO_TOOL_CALL

        ctx.pending_calls = list(response.tool_calls)
        return None

    async def tool_step(self, ctx: "RetropusRunContext") -> Optional[Termination]:
        """Execute pending tool calls and return ``SUBMITTED`` when finish is accepted."""
        from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (  # noqa: PLC0415
            build_retropus_registry,
        )

        calls, ctx.pending_calls = ctx.pending_calls, []
        if not calls:
            return None

        cfg = ctx.retropus_config
        registry = build_retropus_registry(ctx.tools)
        ctx.finish_requested = False

        for call in calls:
            spec = registry.get(call.name)
            call_id = call.call_id or f"call_{ctx.tool_calls_made}"
            if spec is None:
                msg = f"Error: unknown tool '{call.name}'."
                ctx.history.append(ChatMessage(role="tool", tool_call_id=call_id, content=msg))
                continue
            try:
                outcome = await spec.executor(ctx, call.arguments)
            except Exception as e:  # noqa: BLE001
                msg = f"Error executing {call.name}: {e}"
                ctx.history.append(ChatMessage(role="tool", tool_call_id=call_id, content=msg))
                continue
            ctx.tool_calls_made += 1
            ctx.history.append(
                ChatMessage(role="tool", tool_call_id=call_id, content=outcome.message)
            )

        ctx.tools.drain_new_spans()

        if ctx.finish_requested:
            return Termination.SUBMITTED
        if ctx.tool_calls_made >= cfg.max_tool_calls:
            return Termination.MAX_TURNS
        return None

    def pad_spans_from_retriever(self, ctx: "RetropusRunContext", target_count: int) -> None:
        """Add top-ranked definition spans until ``target_count`` spans are recorded.

        Skips duplicates and candidates rejected by ``add_context`` (e.g. test
        ban / missing path). Best-effort: may still finish below target if the
        retriever cannot supply enough acceptable defs.
        """
        have = len(ctx.tools.final_spans())
        if target_count <= have:
            return
        need = target_count - have
        # Over-fetch so ban_tests / rejected paths still leave enough candidates.
        fetch_k = max(target_count * 3, 15)
        try:
            ranked_files = ctx.retriever.score_files_and_defs(
                ctx.issue_text, top_k=fetch_k, max_defs_per_file=10
            )
        except Exception:  # noqa: BLE001
            ranked_files = []

        candidates: list[tuple[float, str, Any]] = []
        for entry in ranked_files:
            rel = entry["file_node"].node.relative_path
            for def_node, score in entry.get("defs") or []:
                candidates.append((float(score), rel, def_node))
        candidates.sort(key=lambda item: item[0], reverse=True)

        reason = (
            "mandatory_fallback" if ctx.retropus_config.min_mandatory_return_spans else "fallback"
        )
        added = 0
        for _score, rel, def_node in candidates:
            if added >= need:
                break
            before = len(ctx.tools.final_spans())
            ctx.tools.add_context(
                rel,
                def_node.node.start_line,
                def_node.node.end_line,
                reason=reason,
            )
            if len(ctx.tools.final_spans()) > before:
                added += 1

        logger.info(
            "Retriever pad added %d span(s) (now %d / target %d)",
            added,
            len(ctx.tools.final_spans()),
            target_count,
        )

    def finalize(self, ctx: "RetropusRunContext", termination: Termination) -> CodeSearchResult:
        """Pad spans if needed, map them to hits, and build the final result."""
        if termination not in (Termination.LLM_ERROR, Termination.INDEX_NOT_READY):
            cfg = ctx.retropus_config
            mandatory = max(0, int(cfg.min_mandatory_return_spans))
            if mandatory > 0:
                have = len(ctx.tools.final_spans())
                if have < mandatory:
                    logger.info(
                        "Padding spans to min_mandatory_return_spans=%d (have %d)",
                        mandatory,
                        have,
                    )
                    self.pad_spans_from_retriever(ctx, target_count=mandatory)
            elif not ctx.tools.has_spans():
                logger.info("No spans recorded; running BM25 fallback")
                self.pad_spans_from_retriever(ctx, target_count=5)

        ctx.termination = termination
        if termination == Termination.INDEX_NOT_READY:
            hits: list[FinalHit] = []
        else:
            limit = min(ctx.top_k, ctx.retropus_config.max_final_spans)
            spans = ctx.tools.final_spans()[: ctx.retropus_config.max_final_spans]
            hits = spans_to_hits(spans, ctx.repo_dir, limit)
        logger.info(
            "Retropus done: termination=%s hits=%d tokens=%din/%dout",
            termination,
            len(hits),
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


class GraphCodeResolveAgent:
    """Agentic Code Resolver executing via openjiuwen Workflow graph."""

    def __init__(self) -> None:
        pass

    async def resolve(self, ctx: CodeResolveRunContext) -> str:
        """Run the resolve loop."""
        from openjiuwen_codesearch.framework.openjiuwen.workflow import (
            RESOLVE_WORKFLOW_ID,
            RESOLVE_WORKFLOW_VERSION,
            GraphCodeSearchAgent,
        )

        from openjiuwen.core.runner.runner import Runner
        from openjiuwen.core.session import workflow_session_vars
        from openjiuwen.core.session.constants import WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY

        # Ensure workflows are registered in the global LegacyWorkflowAgent
        GraphCodeSearchAgent.get_shared_agent()

        with run_resolve_session(ctx) as run_id:
            session_vars = dict(workflow_session_vars.get() or {})
            session_vars[WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY] = str(
                ctx.config.agent.time_limit_seconds
            )
            session_token = workflow_session_vars.set(session_vars)

            try:
                await Runner.run_workflow(
                    workflow=f"{RESOLVE_WORKFLOW_ID}_{RESOLVE_WORKFLOW_VERSION}",
                    inputs={"run_id": run_id, "workflow_name": RESOLVE_WORKFLOW_ID},
                )
                if ctx.result is None:
                    from openjiuwen_codesearch.framework.openjiuwen.steps import finalize_resolve

                    logger.warning(
                        "Resolve Workflow ended without EndNode result; finalizing from context."
                    )
                    finalize_resolve(ctx, ctx.pending_termination or Termination.LLM_ERROR)
            except Exception as e:
                logger.error("Resolve workflow graph execution failed or timed out: %s", e)
                ctx.pending_termination = Termination.LLM_ERROR
                ctx.error = f"Workflow Error: {e}"
                from openjiuwen_codesearch.framework.openjiuwen.steps import finalize_resolve

                finalize_resolve(ctx, ctx.pending_termination)
            finally:
                workflow_session_vars.reset(session_token)

        if not ctx.result:
            return ""
        return ctx.result.patch
