"""无效页面、正文分区与抓取回退的离线测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen_deepsearch.algorithm.research_collector.enrichment_content import prepare_fetched_content, partition_primary_content
from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import build_compression_user_payload
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph import webpage_enrichment as module
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.webpage_cache import TaskWebpageFetchCache

PLACEHOLDER = "Title: Just a moment...\n# Are you a robot?\nPlease confirm you are a human by completing the captcha challenge below.\n" + "script payload " * 300


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    PLACEHOLDER, "<script>" + "x" * 1000 + "</script>",
    "## Related papers\n[First different scientific publication](https://example.com/a)\n"
    + "recommendation " * 30 + "\n[Second different scientific publication](https://example.com/b)\n"
    + "recommendation " * 30,
])
async def test_invalid_page_is_not_cached_or_sent_to_model(monkeypatch, content):
    cache = TaskWebpageFetchCache()
    fetch = AsyncMock(side_effect=[{"content": content}, {"content": "valid article " * 40}])
    node = module.WebPageEnrichmentNode()
    model = AsyncMock()
    monkeypatch.setattr(module, "ainvoke_llm_with_stats", model)
    try:
        assert await cache.get_or_fetch("a", asyncio.get_running_loop().time() + 1, 200, fetch) == {}
        assert cache.snapshot()["cache_entries"] == 0
        assert await node._compress_content({}, {}, {"content": content}) is None
        model.assert_not_called()
        assert await cache.get_or_fetch("a", asyncio.get_running_loop().time() + 1, 200, fetch)
    finally:
        await cache.close()


def test_script_cleaning_preserves_normal_captcha_discussion_and_metadata():
    raw = {"title": "CAPTCHA evaluation", "content": "# CAPTCHA evaluation\nWe discuss how to verify you are human.\n<script>tracker()</script>\nResults: 98%."}
    result, reason = prepare_fetched_content(raw)
    assert not reason and "Results: 98%." in result["content"]
    assert "tracker()" not in result["content"]
    assert "tracker()" in raw["content"] and result["title"] == raw["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_response", [None, {"content": PLACEHOLDER}])
async def test_placeholder_routes_fall_back_with_same_deadline(monkeypatch, direct_response):
    node = module.WebPageEnrichmentNode()
    direct = Mock(return_value=direct_response)
    second = AsyncMock(return_value={"content": PLACEHOLDER})
    reader = Mock(return_value="valid scientific article " * 30)
    node._jina_provider = SimpleNamespace(fetch_page=reader)
    monkeypatch.setattr(module.WebFetchWebpageAdapter, "fetch_webpage_sync", direct)
    monkeypatch.setattr(node, "_fetch_via_simple_ua", second)
    deadline = asyncio.get_running_loop().time() + 5
    result = await node._fetch_webpage_before_deadline("https://example.com", deadline, 200)
    assert result["fetch_method"] == "jina_provider"
    assert second.call_args.args[1] == deadline
    assert reader.call_args.kwargs["budget"] <= 5


@pytest.mark.parametrize("html", [False, True])
def test_recommendation_cards_removed_but_article_sections_and_references_retained(html):
    main = "# Primary study\n\n## Abstract\nOffset decreases from 19mV to 8.5mV at 1.5V.\n\n## Related Work\nPrior studies compare voltage and current sensing."
    related = "## Related papers\n[Different study on HYSA](https://example.com/hysa)\n5120 devices at 0.4V.\n[Another study on CLSA](https://example.com/clsa)\nUnrelated results."
    tail = "## Conclusion\nThe main study uses an input DC level of 70%.\n## References\n[First citation](https://example.com/ref)"
    content = "Title: Primary study\nLogin navigation\n" + main + "\n\n" + related + "\n\n" + tail
    if html:
        import re
        content = re.sub(r"(?m)^(#{1,6}) (.+)$", lambda m: f"<h{len(m[1])}>{m[2]}</h{len(m[1])}>", content)
        content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', content)
    fetched, reason = prepare_fetched_content({"content": content})
    assert not reason and fetched["excluded_content_chars"] > 0
    primary = fetched["primary_content"]
    assert "19mV to 8.5mV" in primary and "input DC level of 70%" in primary
    assert "Related Work" in primary and "References" in primary
    assert "5120 devices" not in primary and "Login navigation" not in primary
    old = {"original_content": "Old evidence about HYSA at 0.4V."}
    payload = build_compression_user_payload({}, old, fetched)
    assert payload["existing_content"] == old["original_content"]
    assert payload["webpage_content"] == primary
    assert payload["webpage_content_scope"] == "primary_article"
    assert "5120 devices" in fetched["content"]  # 原文仍可排查，压缩输入不含推荐区。


def test_ambiguous_related_heading_without_cards_does_not_cut_text():
    content = "# A study\n\n## Related papers\nOur survey compares earlier circuit topologies.\n\n## Results\n19mV to 8.5mV."
    assert partition_primary_content(content) == (content, 0)
