from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    resolve_temporal_embed_in_query,
)


def test_tavily_source_date_no_embed():
    assert resolve_temporal_embed_in_query("tavily", "source_date", scholarly_enabled=False) is False


def test_tavily_source_date_with_scholarly_force_embed():
    assert resolve_temporal_embed_in_query("tavily", "source_date", scholarly_enabled=True) is True


def test_non_tavily_source_date_embed():
    assert resolve_temporal_embed_in_query("bing", "source_date", scholarly_enabled=False) is True


def test_content_date_always_embed():
    assert resolve_temporal_embed_in_query("tavily", "content_date", scholarly_enabled=False) is True
    assert resolve_temporal_embed_in_query("bing", "content_date", scholarly_enabled=True) is True
