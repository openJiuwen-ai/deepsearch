"""Brief 章节批量证据评估的行为测试。"""

import json
import logging
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.evaluation import evaluate_brief_sections
from openjiuwen_deepsearch.algorithm.brief_report.models import BriefOutline


def _outline():
    """构造三个章节的最小可评估 Brief 大纲。"""
    return BriefOutline.model_validate(
        {
            "title": "报告",
            "sections": [
                {
                    "id": "1",
                    "title": "规模",
                    "goal": "验证规模",
                    "research_steps": [
                        {"id": "1-1", "requirement": "验证规模数据", "evidence_type": "data"},
                        {"id": "1-2", "requirement": "验证增长差异", "evidence_type": "comparison"},
                    ],
                },
                {
                    "id": "2",
                    "title": "风险",
                    "goal": "验证风险",
                    "research_steps": [
                        {"id": "2-1", "requirement": "验证政策风险", "evidence_type": "policy"},
                        {"id": "2-2", "requirement": "验证供应风险", "evidence_type": "general"},
                    ],
                },
                {
                    "id": "3",
                    "title": "建议",
                    "goal": "验证建议依据",
                    "research_steps": [
                        {"id": "3-1", "requirement": "验证优先事项", "evidence_type": "general"},
                        {"id": "3-2", "requirement": "验证约束", "evidence_type": "general"},
                    ],
                },
            ],
        }
    )


@pytest.mark.asyncio
async def test_sections_are_evaluated_in_parallel_once_each(monkeypatch):
    """上下文充足时每章仅执行一次批量评估调用。"""
    invoke = AsyncMock(return_value={"content": "{\"selected_docs\":[],\"coverage\":[]}"})
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.evaluation.ainvoke_llm_with_stats", invoke
    )

    result = await evaluate_brief_sections(
        object(), _outline(), {"1": [], "2": [], "3": []}
    )

    assert set(result) == {"1", "2", "3"}
    assert invoke.await_count == 3


@pytest.mark.asyncio
async def test_parse_failure_uses_ranked_fallback_and_unknown_coverage(monkeypatch):
    """解析失败仅降级受影响章节，并保留按搜索排名选择的候选。"""
    invoke = AsyncMock(return_value={"content": "not-json"})
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.evaluation.ainvoke_llm_with_stats", invoke
    )
    candidates = {
        "1": [
            {
                "source_id": "s1",
                "title": "A",
                "url": "https://a.example",
                "snippet": "x",
                "search_rank": 1,
                "section_ids": ["1"],
                "step_ids": ["1-1"],
            }
        ],
        "2": [],
        "3": [],
    }

    result = await evaluate_brief_sections(object(), _outline(), candidates)

    assert result["1"].selected_docs[0].source_id == "s1"
    assert {item.status.value for item in result["1"].coverage} == {"unknown"}


@pytest.mark.asyncio
async def test_nonempty_candidates_use_evaluator(monkeypatch):
    """可哈希性不应使正常候选路径丢失评估结果。"""
    invoke = AsyncMock(
        return_value={
            "content": """{
                "selected_docs": [{
                    "source_id": "s1", "step_ids": ["1-1"],
                    "evaluation_rank": 1
                }],
                "coverage": [
                    {"step_id": "1-1", "status": "covered", "reason": "已覆盖"},
                    {"step_id": "1-2", "status": "weak", "reason": "证据较弱"}
                ]
            }"""
        }
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.evaluation.ainvoke_llm_with_stats", invoke
    )
    candidates = {
        "1": [
            {
                "source_id": "s1", "title": "A", "url": "https://a.example",
                "snippet": "x", "search_rank": 1, "section_ids": ["1"], "step_ids": ["1-1"],
            }
        ],
        "2": [],
        "3": [],
    }

    result = await evaluate_brief_sections(object(), _outline(), candidates)

    assert result["1"].selected_docs[0].source_id == "s1"
    assert result["1"].coverage[0].status.value == "covered"
    assert invoke.await_count == 3


@pytest.mark.asyncio
async def test_context_limit_recursively_splits_candidates_before_retrying(monkeypatch):
    """上下文超限时，评估必须先拆小候选 Prompt 再继续调用。"""
    attempted_sizes = []

    async def invoke(_llm, messages, **_kwargs):
        attempted_sizes.append(len(messages["candidates"]))
        if len(messages["candidates"]) > 1:
            raise RuntimeError("context_length_exceeded")
        candidate = messages["candidates"][0]
        return {"content": json.dumps({
            "selected_docs": [{
                "source_id": candidate["source_id"], "step_ids": ["1-1"],
                "evaluation_rank": 1,
            }],
            "coverage": [{"step_id": "1-1", "status": "covered", "reason": "证据"}],
        })}

    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.evaluation.apply_system_prompt", lambda _name, payload: payload)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.evaluation.ainvoke_llm_with_stats", invoke)
    outline = _outline().model_copy(update={"sections": [_outline().sections[0]]})
    candidates = {"1": [
        {
            "source_id": f"s{index}", "title": str(index), "url": f"https://{index}.example",
            "snippet": "x", "search_rank": index, "section_ids": ["1"], "step_ids": ["1-1"],
        }
        for index in range(1, 5)
    ]}

    result = await evaluate_brief_sections(object(), outline, candidates)

    assert attempted_sizes == [4, 2, 1, 1, 2, 1, 1]
    assert {item.source_id for item in result["1"].selected_docs} == {"s1", "s2", "s3", "s4"}


@pytest.mark.asyncio
async def test_evaluation_logs_attempt_stage_and_prompt_size_on_retry(monkeypatch, caplog):
    """评估重试日志必须说明章节、候选规模、Prompt 大小及失败阶段。"""
    invoke = AsyncMock(side_effect=[
        {"content": "not-json"},
        {"content": json.dumps({
            "selected_docs": [{"source_id": "s1", "step_ids": ["1-1"], "evaluation_rank": 1}],
            "coverage": [{"step_id": "1-1", "status": "covered", "reason": "证据充分"}],
        })},
    ])
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.evaluation.ainvoke_llm_with_stats", invoke
    )
    outline = _outline().model_copy(update={"sections": [_outline().sections[0]]})
    candidates = {
        "1": [{
            "source_id": "s1", "title": "来源", "url": "https://example.com",
            "snippet": "可用证据", "search_rank": 1, "section_ids": ["1"], "step_ids": ["1-1"],
        }]
    }

    with caplog.at_level(logging.INFO, logger="openjiuwen_deepsearch.algorithm.brief_report.evaluation"):
        await evaluate_brief_sections(object(), outline, candidates)

    messages = [record.getMessage() for record in caplog.records]
    assert any("Start section evaluation section_id=1 candidates=1 prompt_chars=" in message for message in messages)
    assert any(
        "Evaluation attempt failed section_id=1 attempt=1/3 stage=json_decode" in message
        for message in messages
    )
    assert any("Evaluation succeeded section_id=1 attempt=2/3" in message for message in messages)
