"""Brief 首轮证据审阅与一次补搜缺口清洗。"""

import json
import logging

from openjiuwen_deepsearch.algorithm.brief_report.collector import _blocking_gaps
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefEvidenceReview,
    BriefReviewRequest,
    BriefSectionWritingGuidance,
    BriefStepCoverage,
    BriefWritingGuidance,
    CoverageStatus,
)
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)


def _fallback_review(request: BriefReviewRequest) -> BriefEvidenceReview:
    """审阅模型不可用时复用首轮评估已给出的确定性补搜缺口。"""
    return BriefEvidenceReview(blocking_gaps=_blocking_gaps(request.collection.section_evidence))


def _validate_review_output(request: BriefReviewRequest, payload: object) -> BriefEvidenceReview:
    """删除审阅模型无权产生的章节、步骤和已覆盖缺口。"""
    if not isinstance(payload, dict):
        raise ValueError("brief evidence review payload must be an object")

    valid_sections = {section.id for section in request.outline.sections}
    step_coverage = {
        coverage.step_id: coverage
        for evidence in request.collection.section_evidence.values()
        for coverage in evidence.coverage
    }
    raw_guidance = payload.get("writing_guidance", {})
    if not isinstance(raw_guidance, dict):
        raise ValueError("brief writing guidance must be an object")
    strategy = str(raw_guidance.get("report_strategy") or "").strip()
    raw_section_guidance = raw_guidance.get("section_guidance", [])
    if not isinstance(raw_section_guidance, list):
        raise ValueError("brief section guidance must be a list")
    section_guidance: list[BriefSectionWritingGuidance] = []
    seen_sections: set[str] = set()
    for raw in raw_section_guidance:
        if not isinstance(raw, dict):
            continue
        section_id = str(raw.get("section_id") or "").strip()
        guidance = str(raw.get("guidance") or "").strip()
        if not section_id:
            continue
        if not guidance:
            continue
        if section_id not in valid_sections:
            continue
        if section_id in seen_sections:
            continue
        seen_sections.add(section_id)
        section_guidance.append(BriefSectionWritingGuidance(section_id=section_id, guidance=guidance))

    raw_gaps = payload.get("blocking_gaps", [])
    if not isinstance(raw_gaps, list):
        raise ValueError("brief blocking gaps must be a list")
    blocking_gaps: list[BriefStepCoverage] = []
    seen_steps: set[str] = set()
    for raw in raw_gaps:
        if not isinstance(raw, dict):
            continue
        try:
            gap = BriefStepCoverage.model_validate(raw)
        except ValueError:
            continue
        existing = step_coverage.get(gap.step_id)
        if existing is None:
            continue
        if existing.status == CoverageStatus.COVERED:
            continue
        if gap.status not in {CoverageStatus.WEAK, CoverageStatus.MISSING}:
            continue
        if not gap.blocking_gap:
            continue
        if gap.step_id in seen_steps:
            continue
        seen_steps.add(gap.step_id)
        blocking_gaps.append(gap)

    return BriefEvidenceReview(
        writing_guidance=BriefWritingGuidance(
            report_strategy=strategy,
            section_guidance=section_guidance,
        ),
        blocking_gaps=blocking_gaps,
    )


def _slim_citation_registry(request: BriefReviewRequest) -> list[dict]:
    """引用注册表瘦身：只保留来源索引字段，剥离全文正文。

    审阅只需判断覆盖缺口与写作指引，注册表的 ``original_content``
    全文（单次可达约 20 万 tokens 的主因）对任务无用。

    Args:
        request: 审阅请求。

    Returns:
        仅含 source_id、index、title、url 的字典列表。
    """
    return [
        {
            "source_id": record.source_id,
            "index": record.index,
            "title": record.title,
            "url": record.url,
        }
        for record in request.collection.citation_registry
    ]


async def review_brief_evidence(request: BriefReviewRequest) -> BriefEvidenceReview:
    """审阅首轮证据，生成编辑指引，并决定是否需要执行唯一一次补搜。"""
    prompt_input = {
        "outline": request.outline.model_dump(),
        "section_evidence": {
            section_id: evidence.model_dump()
            for section_id, evidence in request.collection.section_evidence.items()
        },
        "citation_registry": _slim_citation_registry(request),
        "audience_role": request.audience_role,
        "tone": request.tone,
        "user_format": request.user_format,
    }
    attempts = max(1, Config().service_config.info_collector_max_retry_num)
    for attempt_num in range(1, attempts + 1):
        try:
            messages = apply_system_prompt("brief_evidence_review", prompt_input)
            response = await ainvoke_llm_with_stats(
                request.llm,
                messages,
                agent_name=AgentLlmName.BRIEF_EVIDENCE_REVIEWER.value,
            )
            if not isinstance(response, dict):
                raise ValueError("brief evidence review response must be an object")
            payload = json.loads(normalize_json_output(str(response.get("content", ""))))
            return _validate_review_output(request, payload)
        except Exception as exc:
            logger.warning(
                "[BriefEvidenceReviewer] Evidence review attempt failed; attempt=%d/%d error=%s. %s",
                attempt_num,
                attempts,
                "<detail masked>" if LogManager.is_sensitive() else exc,
                "Retry." if attempt_num < attempts else "No retries remain.",
                exc_info=not LogManager.is_sensitive(),
            )
            continue
    fallback = _fallback_review(request)
    logger.warning(
        "[BriefEvidenceReviewer] Evidence review retries exhausted; use deterministic fallback "
        "blocking_gaps=%d.",
        len(fallback.blocking_gaps),
    )
    return fallback
