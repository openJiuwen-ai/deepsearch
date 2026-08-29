from datetime import date

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    TemporalScope,
    resolve_temporal_embed_in_query,
)

_SD = TemporalScope(
    constraint_type="source_date",
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)
_CD = TemporalScope(
    constraint_type="content_date",
    start_date=date(2020, 1, 1),
    end_date=date(2020, 12, 31),
)


def test_tavily_source_date_no_embed():
    assert resolve_temporal_embed_in_query("tavily", _SD, None, scholarly_enabled=False) is False


def test_tavily_source_date_with_scholarly_force_embed():
    assert resolve_temporal_embed_in_query("tavily", _SD, None, scholarly_enabled=True) is True


def test_non_tavily_source_date_embed():
    assert resolve_temporal_embed_in_query("bing", _SD, None, scholarly_enabled=False) is True


def test_content_date_always_embed():
    assert resolve_temporal_embed_in_query("tavily", None, _CD, scholarly_enabled=False) is True
    assert resolve_temporal_embed_in_query("bing", None, _CD, scholarly_enabled=True) is True


def test_dual_scope_content_date_dominates_embed():
    # content_date present -> always embed, even on Tavily without scholarly.
    assert resolve_temporal_embed_in_query("tavily", _SD, _CD, scholarly_enabled=False) is True


def test_no_scope_never_embeds():
    assert resolve_temporal_embed_in_query("tavily", None, None, scholarly_enabled=False) is False
    assert resolve_temporal_embed_in_query("bing", None, None, scholarly_enabled=True) is False
