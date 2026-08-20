"""Brief 首轮证据审阅服务的行为测试。"""

import logging
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefCitationRecord,
    BriefCollectionResult,
    BriefOutline,
    BriefReviewRequest,
    BriefSectionEvidence,
)
from openjiuwen_deepsearch.algorithm.brief_report.review import review_brief_evidence


def _review_request():
    """构造含一个确定性阻断缺口的首轮证据。"""
    outline = BriefOutline.model_validate(
        {
            "title": "市场报告",
            "sections": [
                {
                    "id": "1",
                    "title": "市场规模",
                    "goal": "说明市场规模与趋势",
                    "research_steps": [
                        {"id": "1-1", "requirement": "确认市场规模", "evidence_type": "data"},
                        {"id": "1-2", "requirement": "确认年度变化", "evidence_type": "comparison"},
                    ],
                },
                {
                    "id": "2",
                    "title": "竞争格局",
                    "goal": "说明主要竞争者",
                    "research_steps": [
                        {"id": "2-1", "requirement": "确认主要竞争者", "evidence_type": "data"},
                        {"id": "2-2", "requirement": "确认竞争差异", "evidence_type": "comparison"},
                    ],
                },
            ],
        }
    )
    collection = BriefCollectionResult(
        section_evidence={
            "1": BriefSectionEvidence.model_validate(
                {
                    "coverage": [
                        {"step_id": "1-1", "status": "covered", "reason": "已有数据"},
                        {
                            "step_id": "1-2",
                            "status": "missing",
                            "reason": "没有同比数据",
                            "blocking_gap": True,
                            "gap_description": "年度同比数据",
                        },
                    ]
                }
            )
        },
        citation_registry=[
            BriefCitationRecord(
                source_id="s1",
                index=1,
                title="市场数据",
                url="https://example.com/data",
                original_content="市场规模数据",
            )
        ],
    )
    return BriefReviewRequest(
        outline=outline,
        collection=collection,
        llm=object(),
        audience_role="业务负责人",
        tone="直接",
        user_format="要点列表",
    )


@pytest.mark.asyncio
async def test_review_returns_editorial_strategy_and_validated_gaps(monkeypatch):
    """审阅应保留合法指引和真正未覆盖的阻断缺口。"""
    invoke = AsyncMock(
        return_value={
            "content": """{
                "writing_guidance": {
                    "report_strategy": "先给结论，再比较月度变化。",
                    "section_guidance": [
                        {"section_id": "1", "guidance": "先呈现可比数据。"},
                        {"section_id": "unknown", "guidance": "应被清理。"}
                    ]
                },
                "blocking_gaps": [
                    {"step_id": "1-2", "status": "missing", "reason": "缺同比", "blocking_gap": true,
                     "gap_description": "年度同比数据"},
                    {"step_id": "1-1", "status": "covered", "reason": "已有", "blocking_gap": true},
                    {"step_id": "9-9", "status": "missing", "reason": "虚构", "blocking_gap": true}
                ]
            }"""
        }
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.review.ainvoke_llm_with_stats", invoke
    )

    review = await review_brief_evidence(_review_request())

    assert review.writing_guidance.report_strategy == "先给结论，再比较月度变化。"
    assert review.writing_guidance.section_guidance[0].section_id == "1"
    assert [gap.step_id for gap in review.blocking_gaps] == ["1-2"]


@pytest.mark.asyncio
async def test_review_failure_falls_back_to_existing_blocking_gaps(monkeypatch, caplog):
    """审阅模型失败时，应使用评估阶段已有的确定性阻断缺口。"""
    invoke = AsyncMock(side_effect=RuntimeError("temporary failure"))
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.review.ainvoke_llm_with_stats", invoke
    )

    with caplog.at_level(logging.WARNING, logger="openjiuwen_deepsearch.algorithm.brief_report.review"):
        review = await review_brief_evidence(_review_request())

    assert review.writing_guidance.report_strategy == ""
    assert [gap.step_id for gap in review.blocking_gaps] == ["1-2"]
    messages = [record.getMessage() for record in caplog.records]
    assert any("Evidence review attempt failed; attempt=1/3" in message for message in messages)
    assert any("Evidence review retries exhausted; use deterministic fallback" in message for message in messages)
