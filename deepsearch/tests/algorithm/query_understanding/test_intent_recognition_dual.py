"""意图识别 schema 双子对象 + 归一化产双字段。

Covers the dual temporal-constraint normalize path:
- dual-scope: both source_date_scope and content_date_scope populated
- invalid scope: silent None (does not lose the rest of the intent)
- legacy temporal_scope: routed to the matching new field by constraint_type
- temporal_scope is never populated by normalize;
  legacy input routes to the new field and the deprecated field stays None.
"""

from datetime import date

from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    _normalize_research_intent,
)


def test_normalize_dual_scope():
    data = {
        "research_query": "疫情回顾",
        "language": "zh-CN",
        "source_date_scope": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        "content_date_scope": {"start_date": "2020-01-01", "end_date": "2022-12-31"},
    }
    ri = _normalize_research_intent(data)

    assert ri.source_date_scope is not None
    assert ri.source_date_scope.start_date == date(2026, 1, 1)
    assert ri.source_date_scope.end_date == date(2026, 12, 31)
    assert ri.source_date_scope.constraint_type == "source_date"
    assert ri.content_date_scope is not None
    assert ri.content_date_scope.start_date == date(2020, 1, 1)
    assert ri.content_date_scope.end_date == date(2022, 12, 31)
    assert ri.content_date_scope.constraint_type == "content_date"
    # dual-scope cannot be represented in the single legacy temporal_scope field
    # → stays None (both new fields present, no legacy routing).
    assert ri.temporal_scope is None


def test_normalize_invalid_scope_silent_none():
    data = {
        "research_query": "x",
        "language": "en-US",
        "task_type": "comparison",
        "content_date_scope": {"start_date": "not-a-date"},
    }
    ri = _normalize_research_intent(data)

    assert ri.content_date_scope is None
    assert ri.source_date_scope is None
    # invalid scope must not lose the rest of the intent
    assert ri.task_type == "comparison"
    # legacy routing only fires when a single new field is populated; invalid → None
    assert ri.temporal_scope is None


def test_normalize_legacy_temporal_scope_routes():
    data = {
        "research_query": "x",
        "language": "en-US",
        "temporal_scope": {
            "constraint_type": "source_date",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
    }
    ri = _normalize_research_intent(data)

    assert ri.source_date_scope is not None
    assert ri.source_date_scope.start_date == date(2026, 1, 1)
    assert ri.source_date_scope.end_date == date(2026, 12, 31)
    assert ri.source_date_scope.constraint_type == "source_date"
    assert ri.content_date_scope is None
    # legacy temporal_scope routes to source_date_scope
    # (asserted above) and the deprecated temporal_scope field stays None (popped).
    assert ri.temporal_scope is None


def test_normalize_legacy_temporal_scope_routes_content():
    data = {
        "research_query": "x",
        "language": "en-US",
        "temporal_scope": {
            "constraint_type": "content_date",
            "end_date": "2019-06-30",
        },
    }
    ri = _normalize_research_intent(data)

    assert ri.content_date_scope is not None
    assert ri.content_date_scope.end_date == date(2019, 6, 30)
    assert ri.content_date_scope.constraint_type == "content_date"
    assert ri.source_date_scope is None
    # legacy temporal_scope routes to content_date_scope
    # (asserted above) and the deprecated temporal_scope field stays None (popped).
    assert ri.temporal_scope is None


def test_normalize_new_field_overrides_legacy_temporal_scope():
    """When the new field is present, it wins and legacy temporal_scope is ignored."""
    data = {
        "research_query": "x",
        "language": "en-US",
        "source_date_scope": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        "temporal_scope": {
            "constraint_type": "content_date",
            "end_date": "2019-06-30",
        },
    }
    ri = _normalize_research_intent(data)

    assert ri.source_date_scope is not None
    assert ri.source_date_scope.start_date == date(2026, 1, 1)
    # legacy content_date is NOT routed because source_date_scope is already set
    assert ri.content_date_scope is None
    # temporal_scope is never populated by normalize;
    # the new field (source_date_scope) holds the constraint.
    assert ri.temporal_scope is None


def test_normalize_preserves_other_intent_fields_with_scope():
    data = {
        "research_query": "低空经济",
        "language": "zh-CN",
        "task_type": "trend_judgement",
        "source_date_scope": {"start_date": "2024-01-01"},
    }
    ri = _normalize_research_intent(data)

    assert ri.task_type == "trend_judgement"
    assert ri.source_date_scope is not None
    assert ri.source_date_scope.start_date == date(2024, 1, 1)
    assert ri.source_date_scope.end_date is None
    # single-scope constraint lives in source_date_scope
    # (asserted above); temporal_scope stays None.
    assert ri.temporal_scope is None
