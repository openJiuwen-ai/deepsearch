from datetime import date
import logging

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent, TemporalScope,
)

SD = TemporalScope(constraint_type="source_date", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
CD = TemporalScope(constraint_type="content_date", start_date=date(2020, 1, 1), end_date=date(2022, 12, 31))


def test_dual_fields_coexist():
    ri = ResearchIntent(source_date_scope=SD, content_date_scope=CD)
    assert ri.source_date_scope == SD
    assert ri.content_date_scope == CD


def test_legacy_dict_routes_by_constraint_type():
    legacy = {"temporal_scope": {"constraint_type": "source_date", "start_date": "2026-01-01", "end_date": "2026-12-31"}}
    ri = ResearchIntent.model_validate(legacy)
    assert ri.source_date_scope is not None
    assert ri.source_date_scope.start_date == date(2026, 1, 1)
    # legacy temporal_scope is popped after routing;
    # the deprecated field stays None so no reader sees a stale single-scope value.
    assert ri.temporal_scope is None


def test_legacy_instance_routes_via_value_based_has_new():
    # 模拟升级前旧实例经 model_dump()：旧键有值、新键显式 None
    pre = {"temporal_scope": {"constraint_type": "content_date", "start_date": "2020-01-01", "end_date": "2022-12-31"},
           "source_date_scope": None, "content_date_scope": None}
    ri = ResearchIntent.model_validate(pre)
    assert ri.content_date_scope is not None
    assert ri.content_date_scope.start_date == date(2020, 1, 1)


@pytest.mark.parametrize(
    "field, bad_type",
    [
        ("source_date_scope", "content_date"),
        ("content_date_scope", "source_date"),
    ],
)
def test_consistency_mismatch_drops_and_warns(caplog, field, bad_type):
    caplog.set_level(logging.WARNING)
    bad = ResearchIntent(
        **{field: TemporalScope(constraint_type=bad_type, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))}
    )
    assert getattr(bad, field) is None
    assert any("mismatch" in r.message for r in caplog.records)
