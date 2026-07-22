#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
from pathlib import Path

from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context

_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

OUTLINE_MODE_ROUTER_SYSTEM_PROMPT = (
    _DEFAULT_PROMPTS_DIR / "outline_mode_router.md"
).read_text(encoding="utf-8").strip()

VALID_OUTLINE_METHODS = {
    ExecutionMethod.PARALLEL.value,
    ExecutionMethod.DEPENDENCY_DRIVING.value,
}


def parse_outline_execution_method(text: str) -> str | None:
    """
    Parse the outline mode router label from LLM output.

    Args:
        text: Raw LLM output content.

    Returns:
        ``parallel`` or ``dependency_driving`` when the output exactly matches a
        supported outline execution method; otherwise ``None``.
    """
    label = (text or "").strip()
    return label if label in VALID_OUTLINE_METHODS else None


async def route_outline_execution_method(question: str, llm_model_name: str) -> str:
    """
    Route a research query to the actual outline execution method.

    The framework layer decides when this router should be called. This function
    only invokes the configured LLM with the original user query and parses the
    returned mode label.

    Args:
        question: Original user query used for outline mode selection.
        llm_model_name: LLM entry name to read from ``llm_context``.

    Returns:
        ``parallel`` or ``dependency_driving``. Empty queries, LLM call failures,
        and unparseable model outputs fail open to ``parallel``.
    """
    log = logging.getLogger(__name__)
    q = (question or "").strip()
    if not q:
        log.warning("outline mode router: empty question; defaulting to parallel")
        return ExecutionMethod.PARALLEL.value

    messages = [
        {"role": "system", "content": OUTLINE_MODE_ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": q},
    ]

    try:
        llm = llm_context.get().get(llm_model_name)
        resp = await ainvoke_llm_with_stats(
            llm,
            messages,
            llm_type="basic",
            agent_name=AgentLlmName.OUTLINE_MODE_ROUTER.value,
            tools=None,
        )
        content = (resp.get("content") or "").strip() if isinstance(resp, dict) else ""
    except Exception as e:
        log.warning(
            "outline mode router LLM call failed, defaulting to parallel: %s",
            e,
            exc_info=True,
        )
        return ExecutionMethod.PARALLEL.value

    selected_method = parse_outline_execution_method(content)
    if selected_method is None:
        log.warning(
            "outline mode router expected parallel or dependency_driving; got %r, defaulting to parallel",
            content,
        )
        return ExecutionMethod.PARALLEL.value
    return selected_method
