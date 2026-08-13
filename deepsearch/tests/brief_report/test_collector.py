"""Brief 统一证据采集及引用注册表的行为测试。"""

from datetime import date
from unittest.mock import AsyncMock

import logging

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.collector import (
    build_citation_registry,
    collect_initial_brief_evidence,
    generate_brief_queries,
    supplement_brief_evidence,
)
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefCollectorRequest,
    BriefOutline,
    BriefQuery,
    BriefQueryRequest,
    BriefSearchResult,
    BriefSectionEvidence,
)


def _collector_outline():
    """构造三章、每章两步骤的采集测试大纲。"""
    return BriefOutline.model_validate(
        {
            "title": "测试报告",
            "sections": [
                {
                    "id": str(index),
                    "title": f"章节 {index}",
                    "goal": f"验证目标 {index}",
                    "research_steps": [
                        {"id": f"{index}-1", "requirement": "验证指标", "evidence_type": "data"},
                        {"id": f"{index}-2", "requirement": "验证差异", "evidence_type": "comparison"},
                    ],
                }
                for index in range(1, 4)
            ],
        }
    )


def _collector_request():
    """创建不依赖真实网络或模型的采集请求。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ResearchIntent

    return BriefCollectorRequest(
        outline=_collector_outline(),
        user_query="测试",
        research_intent=ResearchIntent().model_dump(),
        llm=object(),
    )


def _blocking_evidence_for_section_one():
    """构造第一章存在一个阻断缺口的评估结果。"""
    return {
        "1": BriefSectionEvidence.model_validate(
            {
                "selected_docs": [],
                "coverage": [
                    {
                        "step_id": "1-1",
                        "status": "missing",
                        "reason": "required",
                        "blocking_gap": True,
                        "gap_description": "missing metric",
                    },
                    {"step_id": "1-2", "status": "covered", "reason": "supported"},
                ],
            }
        )
    }


def _covered_evidence_for_section_one():
    """构造补搜后第一章被补齐的评估结果。"""
    return {
        "1": BriefSectionEvidence.model_validate(
            {
                "selected_docs": [],
                "coverage": [
                    {"step_id": "1-1", "status": "covered", "reason": "supplemented"},
                    {"step_id": "1-2", "status": "covered", "reason": "supported"},
                ],
            }
        )
    }


def _selected_results_with_duplicate_url():
    """构造两章选中同 URL 不同可见摘要的输入。"""
    results = [
        BriefSearchResult(
            source_id="s1",
            title="A",
            url="https://example.com/a",
            snippet="snippet one",
            search_rank=1,
            section_ids=["1"],
            step_ids=["1-1"],
        ),
        BriefSearchResult(
            source_id="s2",
            title="A",
            url="https://example.com/a",
            snippet="snippet two",
            search_rank=1,
            section_ids=["2"],
            step_ids=["2-1"],
        ),
    ]
    evidence = {
        "1": BriefSectionEvidence.model_validate(
            {
                "selected_docs": [
                    {"source_id": "s1", "step_ids": ["1-1"], "evaluation_rank": 1}
                ],
                "coverage": [],
            }
        ),
        "2": BriefSectionEvidence.model_validate(
            {
                "selected_docs": [
                    {"source_id": "s2", "step_ids": ["2-1"], "evaluation_rank": 1}
                ],
                "coverage": [],
            }
        ),
    }
    return results, evidence


@pytest.mark.asyncio
async def test_supplement_collection_merges_node_supplied_search_results(monkeypatch):
    """补搜的 Query 和搜索结果应由节点提供，算法层只重评受影响章节。"""
    evaluate = AsyncMock(side_effect=[_blocking_evidence_for_section_one(), _covered_evidence_for_section_one()])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.collector.evaluate_brief_sections", evaluate)

    request = _collector_request()
    initial_queries = [BriefQuery(query="market size", section_ids=["1"], step_ids=["1-1"])]
    supplementary = [
        BriefQuery(query="2025 audited market size", section_ids=["1"], step_ids=["1-1"]),
        BriefQuery(query="duplicate", section_ids=["1"], step_ids=["1-1"]),
        BriefQuery(query="risk", section_ids=["2"], step_ids=["2-1"]),
        BriefQuery(query="policy", section_ids=["2"], step_ids=["2-2"]),
        BriefQuery(query="also search", section_ids=["3"], step_ids=["3-1"]),
    ]
    collection, context = await collect_initial_brief_evidence(request, initial_queries, [])
    result, updated_context = await supplement_brief_evidence(
        request, collection, context, supplementary, []
    )

    assert [section.id for section in evaluate.await_args_list[1].args[1].sections] == ["1", "2", "3"]
    assert evaluate.await_count == 2
    assert updated_context.executed_queries == [
        "market size",
        "2025 audited market size",
        "duplicate",
        "risk",
        "policy",
        "also search",
    ]


def test_citation_registry_uses_one_id_per_url_and_merges_visible_snippets():
    """同 URL 的引用 ID 稳定复用，并只合并模型实际可见的摘要。"""
    registry = build_citation_registry(*_selected_results_with_duplicate_url())

    assert len(registry) == 1
    assert registry[0].index == 1
    assert "snippet one" in registry[0].original_content
    assert "snippet two" in registry[0].original_content


@pytest.mark.asyncio
async def test_query_generation_retries_transient_llm_failure(monkeypatch):
    """正式 Query 生成应沿用既有采集重试配置。"""
    invoke = AsyncMock(side_effect=[RuntimeError("temporary"), {"content": '{"queries":[{"query":"规模","section_ids":["1"],"step_ids":["1-1"]}]}' }])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.collector.ainvoke_llm_with_stats", invoke)

    queries = await generate_brief_queries(
        object(),
        BriefQueryRequest(outline=_collector_outline(), user_query="测试"),
    )

    assert [item.query for item in queries] == ["规模"]
    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_query_generation_logs_each_failed_attempt_before_retry(monkeypatch, caplog):
    """Query 重试必须留下失败原因和重试序号，便于定位采集失败。"""
    invoke = AsyncMock(side_effect=[
        RuntimeError("temporary"),
        {"content": '{"queries":[{"query":"规模","section_ids":["1"],"step_ids":["1-1"]}]}'},
    ])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.collector.ainvoke_llm_with_stats", invoke)

    with caplog.at_level(logging.WARNING, logger="openjiuwen_deepsearch.algorithm.brief_report.collector"):
        queries = await generate_brief_queries(
            object(), BriefQueryRequest(outline=_collector_outline(), user_query="测试")
        )

    assert [item.query for item in queries] == ["规模"]
    assert any(
        "Query generation attempt failed; attempt=1/3" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_query_generation_accepts_temporal_scope_with_date_objects(monkeypatch):
    """正式与补充搜索共用的 Query Prompt 必须接收统一时间上下文。"""
    invoke = AsyncMock(return_value={
        "content": '{"queries":[{"query":"2024 市场规模","section_ids":["1"],"step_ids":["1-1"]}]}'
    })
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.collector.ainvoke_llm_with_stats", invoke)

    queries = await generate_brief_queries(
        object(),
        BriefQueryRequest(
            outline=_collector_outline(),
            user_query="测试时间边界",
            research_intent={
                "temporal_scope": {
                    "constraint_type": "content_date",
                    "end_date": date(2024, 12, 31),
                }
            },
        ),
    )

    assert [item.query for item in queries] == ["2024 市场规模"]
    assert "on or before 2024-12-31" in invoke.await_args.args[1][0]["content"]


@pytest.mark.asyncio
async def test_query_generation_retries_invalid_query_container(monkeypatch):
    """JSON 可解析但 queries 结构错误时也必须重试。"""
    invoke = AsyncMock(side_effect=[
        {"content": '{"queries": {}}'},
        {"content": '{"queries":[{"query":"规模","section_ids":["1"],"step_ids":["1-1"]}]}'},
    ])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.collector.ainvoke_llm_with_stats", invoke)

    queries = await generate_brief_queries(
        object(), BriefQueryRequest(outline=_collector_outline(), user_query="测试")
    )

    assert [item.query for item in queries] == ["规模"]
    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_query_generation_retries_when_cleaning_removes_every_query(monkeypatch):
    """列表存在但元素全部无效时，不能把空列表当作可用结果。"""
    invoke = AsyncMock(side_effect=[
        {"content": '{"queries":[{"query":"","section_ids":[],"step_ids":[]}]}'},
        {"content": '{"queries":[{"query":"规模","section_ids":["1"],"step_ids":["1-1"]}]}'},
    ])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.collector.ainvoke_llm_with_stats", invoke)

    queries = await generate_brief_queries(
        object(), BriefQueryRequest(outline=_collector_outline(), user_query="测试")
    )

    assert [item.query for item in queries] == ["规模"]
    assert invoke.await_count == 2
