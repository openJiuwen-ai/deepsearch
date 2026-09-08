# -*- coding: UTF-8 -*-
"""Contract tests for the report selection entry's content_date_scope resolver.

The report selection entry and the ``extract_content_time``
gate resolve content_date via ``_resolve_content_date_scope``. These
tests pin the resolver contract the entry depends on:

* a ``content_date_scope`` (or legacy ``temporal_scope`` with
  ``constraint_type == "content_date"``) resolves to a non-None scope;
* a ``source_date``-only intent resolves to ``None`` for content-date selection,
  so the entry feeds ``TemporalSelectionOptions(temporal_scope=None, ...)`` and
  the internal ``use_temporal`` gate stays off in production.

These are contract-verification
tests (GREEN on the existing implementation), not RED-driving tests.
"""
from datetime import date

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    _resolve_content_date_scope,
)


def test_entry_resolves_content_date_scope_not_legacy():
    ri = ResearchIntent(
        content_date_scope=TemporalScope(
            constraint_type="content_date",
            start_date=date(2020, 1, 1),
            end_date=date(2022, 12, 31),
        )
    )
    scope = _resolve_content_date_scope(ri)
    assert scope is not None
    assert scope.start_date == date(2020, 1, 1)
    assert scope.end_date == date(2022, 12, 31)


def test_entry_source_date_only_returns_none_for_content():
    ri = ResearchIntent(
        source_date_scope=TemporalScope(
            constraint_type="source_date",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    )
    assert _resolve_content_date_scope(ri) is None


def test_entry_legacy_temporal_scope_content_date_routes_to_new_field():
    """Instance-form legacy ``temporal_scope`` (content_date) is routed by the
    before-validator into ``content_date_scope`` (then popped); the resolver reads
    the routed field, so existing flows keep extracting/weighting content_time."""
    ri = ResearchIntent(
        temporal_scope=TemporalScope(
            constraint_type="content_date",
            start_date=date(2018, 1, 1),
            end_date=date(2023, 12, 31),
        )
    )
    scope = _resolve_content_date_scope(ri)
    assert scope is not None
    assert scope.start_date == date(2018, 1, 1)
