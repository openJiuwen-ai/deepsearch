"""Intent temporal classification regression set (llm-marked, opt-in).

Run with: RUN_LLM_TESTS=1 uv run pytest tests/algorithm/query_understanding/test_intent_temporal_llm.py

Requires the general LLM (Config().agent_config.llm_config["general"]) to be
configured with credentials in the local env. Cases are generic rewrites of
recurring temporal-constraint patterns, covering content_date / source_date /
none, single/double boundaries, as-of snapshots, and "research-results-before"
traps. Two cases keep boundaries from the original calibration set as
regression anchors; the rest use neutral years. Prompt examples in
intent_recognition.md intentionally use different years (2014/2016/2017) so
example and regression inputs stay disjoint.
"""
from __future__ import annotations

import os

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    classify_and_recognize_intent,
)
from openjiuwen_deepsearch.algorithm.report_template.template_generator import create_llm_obj
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context

pytestmark = pytest.mark.llm

# (query, expected_constraint_type | None, expected_start_iso | None, expected_end_iso | None)
CASES = [
    ("总结2024年全球电动汽车市场的主要趋势", "content_date", None, "2024-12-31"),
    ("Review the major advances in quantum computing during 2024", "content_date", "2024-01-01", "2024-12-31"),
    ("用2024年发表的综述文章分析该方法的有效性", "source_date", "2024-01-01", "2024-12-31"),
    # 以下两条保留校准集边界，作为陷阱句式与 source_date 区间的回归锚点
    ("考察截至2021年11月之前该领域的研究进展", "content_date", None, "2021-10-31"),
    ("only use literature published between 2020 and 2023", "source_date", "2020-01-01", "2023-12-31"),
    ("分析2018年至2024年期间的政策演变", "content_date", "2018-01-01", "2024-12-31"),
    ("调研截至2024年底的行业现状", "content_date", None, "2024-12-31"),
    ("summarize information available as of 2024", "source_date", None, "2024-12-31"),
    ("Review research breakthroughs in solar cells from 2018 to 2021", "content_date", "2018-01-01", "2021-12-31"),
    ("只用2023年6月之前发布的官方报告", "source_date", None, "2023-05-31"),
    ("梳理2019年以来的技术演进脉络", "content_date", "2019-01-01", None),
    ("概述该领域的核心理论框架", None, None, None),
    ("分析2018-2024年该行业的竞争格局", "content_date", "2018-01-01", "2024-12-31"),
    ("只参考截至2024年底可得的英文文献", "source_date", None, "2024-12-31"),
    ("综述2020年前关于该主题的关键发现", "content_date", None, "2019-12-31"),
    ("总结2019年之前该领域的核心研究成果", "content_date", None, "2018-12-31"),
    ("综述2025年前该方向的关键研究进展", "content_date", None, "2024-12-31"),
]


def _require_intent_llm_env():
    if os.getenv("RUN_LLM_TESTS", "").strip() != "1":
        pytest.skip("Set RUN_LLM_TESTS=1 to run intent LLM regression")
    cfg = Config()
    general = cfg.agent_config.llm_config.get("general")
    if general is None:
        pytest.skip("general LLM slot not configured")
    if not getattr(general, "api_key", None):
        pytest.skip("general LLM api_key not configured")
    return general


def _assert_date(actual, expected_iso: str | None) -> None:
    if expected_iso is None:
        assert actual is None, f"expected None boundary, got {actual}"
    else:
        assert actual is not None, f"expected {expected_iso}, got None"
        assert actual.isoformat() == expected_iso, f"{actual.isoformat()} != {expected_iso}"


@pytest.mark.parametrize("query,expected_type,expected_start,expected_end", CASES)
@pytest.mark.asyncio
async def test_intent_temporal_classification(query, expected_type, expected_start, expected_end):
    general = _require_intent_llm_env()
    token = llm_context.set({general.model_name: create_llm_obj(general)})
    try:
        result = await classify_and_recognize_intent({
            "original_query": query,
            "llm_model_name": general.model_name,
            "messages": [],
        })
    finally:
        llm_context.reset(token)

    if expected_type is None:
        assert result.research_intent.source_date_scope is None, f"expected no temporal_scope for: {query}"
        assert result.research_intent.content_date_scope is None, f"expected no temporal_scope for: {query}"
        return
    field = "source_date_scope" if expected_type == "source_date" else "content_date_scope"
    scope = getattr(result.research_intent, field)
    assert scope is not None, f"expected temporal_scope for: {query}"
    assert scope.constraint_type == expected_type, (
        f"{query!r}: constraint_type {scope.constraint_type} != {expected_type}"
    )
    _assert_date(scope.start_date, expected_start)
    _assert_date(scope.end_date, expected_end)
