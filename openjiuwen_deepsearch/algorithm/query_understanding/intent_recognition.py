# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from typing import Literal

from pydantic import BaseModel, Field
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ReportTypePolicy, ResearchIntent
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.utils.common_utils.url_utils import extract_domain_from_url
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

EMIT_INTENT_TOOL = "emit_report_intent"

_VALID_REPORT_TYPES = frozenset({"professional", "brief"})


def normalize_report_type(raw: str | None) -> str | None:
    """归一化报告类型字段。

    Only explicit enum values are accepted:
    - "professional" / "brief" -> normalized value
    - empty/unknown/alias -> None (means "not explicitly specified")

    NOTE:
    Keeping None is intentional. Downstream `generate_questions` uses this signal
    to force a clarification question asking user to choose professional vs brief,
    while policy resolution still defaults to professional when needed.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in _VALID_REPORT_TYPES:
        return s
    return None


def resolve_report_type_policy(
        normalized_report_type: str | None,
) -> ReportTypePolicy:
    """按报告类型解析策略。"""
    if normalized_report_type == "brief":
        return ReportTypePolicy(
            report_type="brief",
            paragraph_style="concise",
            require_summary_first=True,
            require_methodology_and_risk=True,
        )
    return ReportTypePolicy(
        report_type="professional",
        paragraph_style="detailed",
        require_summary_first=False,
        require_methodology_and_risk=False,
    )


class IntentRecognitionResult(BaseModel):
    """
    意图识别完整结果：保留原始输入并拆分研究主题与报告约束。
    """
    original_query: str = Field(default="", description="用户原始输入，完整保留")
    research_query: str = Field(default="", description="用于检索与规划的研究主题")
    research_intent: ResearchIntent = Field(default_factory=ResearchIntent, description="结构化报告约束")


def _default_fallback(original_query: str | None) -> IntentRecognitionResult:
    text = original_query if original_query is not None else ""
    stripped = (text or "").strip()
    return IntentRecognitionResult(
        original_query=text,
        research_query=stripped,
        research_intent=ResearchIntent(),
    )


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _normalize_research_intent(data: dict) -> ResearchIntent:
    raw_section = data.get("section_count")
    section_count = None
    if raw_section is not None:
        try:
            n = int(raw_section)
            if n > 0:
                section_count = n
        except (TypeError, ValueError):
            pass

    tone_raw = data.get("tone")
    tone = str(tone_raw).strip().lower() if tone_raw is not None and str(tone_raw).strip() else None

    rt_raw = data.get("report_type")
    report_type = normalize_report_type(str(rt_raw).strip() if rt_raw is not None else None)

    ar_raw = data.get("audience_role")
    audience_role = str(ar_raw).strip() if ar_raw is not None and str(ar_raw).strip() else None

    include_url = _dedupe_preserve_order(list(data.get("include_url") or []))
    exclude_url = _dedupe_preserve_order(list(data.get("exclude_url") or []))
    include_domains = _dedupe_preserve_order(
        [str(d).strip() for d in (data.get("include_domains") or []) if str(d).strip()]
    )
    exclude_domains = _dedupe_preserve_order(
        [str(d).strip() for d in (data.get("exclude_domains") or []) if str(d).strip()]
    )

    for url in include_url:
        domain = extract_domain_from_url(url)
        if domain and domain not in include_domains:
            include_domains.append(domain)
    include_domains = _dedupe_preserve_order(include_domains)

    return ResearchIntent(
        section_count=section_count,
        audience_role=audience_role,
        tone=tone,
        report_type=report_type,
        include_url=include_url,
        exclude_url=exclude_url,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )


async def _emit_report_intent(**kwargs) -> IntentRecognitionResult:
    """将 LLM tool_call args 转换为意图识别结果。"""
    research_query = (kwargs.get("research_query") or "").strip()

    return IntentRecognitionResult(
        research_query=research_query,
        research_intent=_normalize_research_intent(kwargs),
    )


def _create_emit_intent_tool() -> LocalFunction:
    card = ToolCard(
        id=EMIT_INTENT_TOOL,
        name=EMIT_INTENT_TOOL,
        description=(
            "Emit structured report constraints and the cleaned research_query. "
            "You MUST call this tool exactly once."
        ),
        input_params={
            "type": "object",
            "properties": {
                "research_query": {
                    "type": "string",
                    "description": (
                        "The core research topic only (what to investigate). "
                        "Exclude meta instructions about chapters, audience, tone, or URLs. "
                        "Keep the same language as the user's original query and do not translate."
                    ),
                },
                "section_count": {
                    "type": "integer",
                    "description": "Max or desired number of sections/chapters if user specified; else omit.",
                },
                "audience_role": {
                    "type": "string",
                    "description": "Target reader role (keep user's wording or short label).",
                },
                "tone": {
                    "type": "string",
                    "description": (
                        "Writing tone as English enum: objective, formal, analytical, informative, "
                        "explanatory, persuasive, etc."
                    ),
                },
                "report_type": {
                    "type": "string",
                    "enum": ["professional", "brief"],
                    "description": (
                        "Report type. MUST be exactly 'professional' (full deep research) "
                        "or 'brief' (concise). Map user wording (e.g. 精简版/深度研究) to these "
                        "values before emitting. Omit if unclear."
                    ),
                },
                "include_url": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full URLs the user explicitly provided or wants to prioritize.",
                },
                "exclude_url": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs the user wants to exclude.",
                },
                "include_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domains to prefer (hostname only, lowercase, no scheme).",
                },
                "exclude_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domains to exclude.",
                },
            },
            "required": ["research_query"],
        },
    )

    return LocalFunction(card=card, func=_emit_report_intent)


async def recognize_report_intent(current_inputs: dict) -> IntentRecognitionResult:
    """
    使用 LLM + 单次 tool call 解析报告意图与研究主题。

    Args:
        current_inputs: 需包含 ``original_query``；可选 ``messages``、``llm_model_name``。

    Returns:
        IntentRecognitionResult: LLM 失败或无 tool call 时回退为 research_query=original_query、空 intent。
    """
    original_query = current_inputs.get("original_query").strip()
    if not original_query:
        return _default_fallback(original_query)

    prompt_ctx = {
        "original_query": original_query,
        "messages": current_inputs.get("messages") or [],
    }
    prompts = apply_system_prompt("intent_recognition", prompt_ctx)

    tool = _create_emit_intent_tool()
    try:
        llm = llm_context.get().get(current_inputs.get("llm_model_name"))
        response = await llm_utils.ainvoke_llm_with_stats(
            llm,
            prompts,
            llm_type="basic",
            agent_name=AgentLlmName.INTENT_RECOGNITION.value,
            tools=[tool.card.tool_info()],
            need_stream_out=False,
        )
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            logger.warning("[recognize_report_intent] No tool_calls in LLM response, using fallback.")
            return _default_fallback(original_query)

        tool_call = tool_calls[0]
        if tool_call.get("name") and tool_call.get("name") != tool.card.name:
            logger.warning(
                "[recognize_report_intent] Tool name is not match(%s): %s",
                tool.card.name,
                "**" if LogManager.is_sensitive() else tool_call.get("name"),
            )

        args = tool_call.get("args") or {}
        if not isinstance(args, dict):
            logger.warning("[recognize_report_intent] Invalid tool args type, using fallback.")
            return _default_fallback(original_query)

        tool_result = await tool.invoke(args)
        result = tool_result.model_copy(
            update={
                "original_query": original_query,
                "research_query": tool_result.research_query or original_query,
            }
        )
        if LogManager.is_sensitive():
            logger.info("[recognize_report_intent] parsed successfully (redacted).")
        else:
            logger.info(
                f"[recognize_report_intent] original_query={original_query}\n"
                f"research_query={result.research_query}\n"
                f"intent={result.research_intent.model_dump()}"
            )
        return result

    except Exception as exc:
        if LogManager.is_sensitive():
            logger.warning("[recognize_report_intent] Exception, using fallback.")
        else:
            logger.warning("[recognize_report_intent] Exception, using fallback: %s", exc)
        return _default_fallback(original_query)
