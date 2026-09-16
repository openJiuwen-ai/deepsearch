"""Brief 精简大纲的生成和确定性修复服务。"""

import json
from typing import Any

from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefOutline,
    BriefOutlineRequest,
    EvidenceType,
    OutputFormat,
)
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    build_research_intent_prompt_context,
    build_temporal_scope_prompt_context,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName


def _normalize_outline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """过滤无效章节，并为 ID、枚举和可选字段提供确定性默认值。

    Args:
        payload: LLM 返回的原始 JSON 对象。

    Returns:
        满足 Brief 大纲模型输入约束的字典；不足两章由调用者决定是否重试。
    """
    normalized_sections: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    allowed_evidence_types = {item.value for item in EvidenceType}
    allowed_output_formats = {item.value for item in OutputFormat}

    raw_sections = payload.get("sections", [])
    if not isinstance(raw_sections, list):
        raw_sections = []

    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            continue
        title = str(raw_section.get("title") or "").strip()
        goal = str(raw_section.get("goal") or "").strip()
        normalized_title = title.casefold()
        if not title or not goal or normalized_title in seen_titles:
            continue

        steps: list[dict[str, str]] = []
        raw_steps = raw_section.get("research_steps", [])
        if not isinstance(raw_steps, list):
            raw_steps = []
        for raw_step in raw_steps[:4]:
            if not isinstance(raw_step, dict):
                continue
            requirement = str(raw_step.get("requirement") or "").strip()
            if not requirement:
                continue
            evidence_type = str(raw_step.get("evidence_type") or EvidenceType.GENERAL.value)
            if evidence_type not in allowed_evidence_types:
                evidence_type = EvidenceType.GENERAL.value
            steps.append(
                {
                    "id": f"{len(normalized_sections) + 1}-{len(steps) + 1}",
                    "requirement": requirement,
                    "evidence_type": evidence_type,
                }
            )
        if len(steps) < 2:
            continue

        raw_formats = raw_section.get("output_formats", [])
        if not isinstance(raw_formats, list):
            raw_formats = []
        formats = [item for item in raw_formats if item in allowed_output_formats]
        seen_titles.add(normalized_title)
        normalized_sections.append(
            {
                "id": str(len(normalized_sections) + 1),
                "title": title,
                "goal": goal,
                "research_steps": steps,
                "output_formats": formats or [OutputFormat.PARAGRAPH.value],
                "format_note": str(raw_section.get("format_note") or "")[:240],
            }
        )
    return {
        "title": str(payload.get("title") or "").strip(),
        "sections": normalized_sections,
    }


async def generate_brief_outline(llm: object, request: BriefOutlineRequest) -> BriefOutline:
    """生成并校验 Brief 精简大纲。

    Args:
        llm: `plan_understanding` 槽位的运行时 LLM。
        request: 用户问题、语言和结构化意图。

    Returns:
        可直接供采集和写作消费的 Brief 大纲。

    Raises:
        ValueError: 重试耗尽后仍少于两个有效章节或全部步骤无效。
    """
    prompt_context = request.model_dump(exclude={"research_intent"})
    prompt_context.update(build_research_intent_prompt_context(request.research_intent))
    prompt_context.update(build_temporal_scope_prompt_context(request.research_intent))
    messages = apply_system_prompt("brief_outliner", prompt_context)
    attempts = max(1, Config().service_config.outliner_max_generate_outline_retry_num)
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            response = await ainvoke_llm_with_stats(
                llm,
                messages,
                agent_name=AgentLlmName.BRIEF_OUTLINE.value,
            )
            if not isinstance(response, dict):
                raise ValueError("brief outline response must be an object")
            payload = json.loads(normalize_json_output(str(response.get("content", ""))))
            if not isinstance(payload, dict):
                raise ValueError("brief outline payload must be an object")
            normalized = _normalize_outline_payload(payload)
            if len(normalized["sections"]) < 2:
                raise ValueError("brief outline requires at least two valid sections")
            # Prompt 不限制最大章节数；清洗后仍有二章时按既有失败边界继续，
            # 不为可修复字段额外消耗一次 LLM 调用。
            return BriefOutline.model_validate(normalized)
        except Exception as exc:  # LLM 适配器异常需按既有大纲重试预算处理。
            last_error = exc
    raise ValueError(f"brief outline generation failed: {last_error}") from last_error
