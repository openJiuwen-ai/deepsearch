#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "algorithm" / "prompts"
DEFAULT_QUESTIONS_FILE = _REPO_ROOT / "benchmarking" / "deepsearch_demo_router_questions.jsonl"

QUESTION_ROUTER_SYSTEM_PROMPT = (
    _DEFAULT_PROMPTS_DIR / "question_model_router.md"
).read_text(encoding="utf-8").strip()


def parse_bit(text: str) -> int | None:
    t = (text or "").strip()
    if not t:
        return None
    if re.fullmatch(r"[01]", t):
        return int(t)
    m = re.search(r"[01]", t)
    return int(m.group(0)) if m else None


async def route_question_search_path(
    question: str,
    llm_entry: dict,
    *,
    extra_body: dict | None = None,
) -> int:
    """
    In-process router using the same LLM stack as the app.
    Returns ``0`` → simple ReAct path; ``1`` → full DeepSearch.
    On LLM failure or unparseable output, returns ``1`` (safe default).
    """
    import logging
    from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
    from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

    log = logging.getLogger(__name__)
    q = (question or "").strip()
    if not q:
        log.warning("question router: empty question; defaulting to DeepSearch (1)")
        return 1

    messages = [
        {"role": "system", "content": QUESTION_ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": q},
    ]
    try:
        resp = await ainvoke_llm_with_stats(
            llm_entry,
            messages,
            llm_type="basic",
            agent_name=AgentLlmName.QUESTION_MODEL_ROUTER.value,
            tools=None,
            extra_body=extra_body,
        )
        content = (resp.get("content") or "").strip() if isinstance(resp, dict) else ""
    except Exception as e:
        log.warning(
            "question router LLM call failed, defaulting to DeepSearch (1): %s",
            e,
            exc_info=True,
        )
        return 1
    bit = parse_bit(content)
    if bit is None:
        log.warning(
            "question router expected 0 or 1; got %r — defaulting to DeepSearch (1)",
            content,
        )
        return 1
    return bit
