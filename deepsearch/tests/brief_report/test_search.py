"""Brief 报告级搜索结果标准化与候选路由的行为测试。"""

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefQuery
from openjiuwen_deepsearch.algorithm.brief_report.search import (
    build_section_candidates,
    normalize_brief_search_results,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ResearchIntent

def test_normalized_search_results_merge_same_url_with_only_section_step_routing():
    """工具已执行的结果合并后只保留章节和步骤路由。"""
    queries = [
        BriefQuery(query="alpha", section_ids=["1"], step_ids=["1-1"]),
        BriefQuery(query="beta", section_ids=["2"], step_ids=["2-1"]),
    ]

    results = normalize_brief_search_results(
        [
            (queries[0], [{"title": "Source", "url": "https://example.com/a?utm_source=x", "content": "alpha"}]),
            (queries[1], [{"title": "Source", "url": "https://example.com/a", "content": "beta"}]),
        ],
        ResearchIntent(),
    )

    assert len(results) == 1
    assert results[0].section_ids == ["1", "2"]
    assert "query_ids" not in results[0].model_dump()
    assert "alpha" in results[0].snippet and "beta" in results[0].snippet


def test_build_section_candidates_never_routes_to_unlinked_section():
    """未被 Query 显式关联的章节不得收到该候选。"""
    result = {
        "source_id": "s1",
        "title": "Source",
        "url": "https://example.com/a",
        "snippet": "evidence",
        "search_rank": 1,
        "section_ids": ["1"],
        "step_ids": ["1-1"],
    }

    candidates = build_section_candidates([result], ["1", "2"])

    assert [item.source_id for item in candidates["1"]] == ["s1"]
    assert candidates["2"] == []
