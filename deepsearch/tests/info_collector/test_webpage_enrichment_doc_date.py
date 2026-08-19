import asyncio
from unittest.mock import Mock, patch

import pytest
import requests

from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import (
    WebPageEvidenceContent,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import (
    WebPageEnrichmentNode,
)

_HEAD_FETCH_TARGET = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
    "webpage_enrichment._fetch_html_document"
)
_DIRECT_FETCH_TARGET = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
    "webpage_enrichment.WebFetchWebpageAdapter.fetch_webpage_sync"
)
_JINA_FETCH_TARGET = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
    "webpage_enrichment.WebFetchWebpageAdapter.fetch_via_jina_reader_sync"
)

_HEAD_HTML_WITHOUT_DATE = """
<html><head>
<title>No date here</title>
<meta name="description" content="plain page" />
</head><body><p>body</p></body></html>
"""


class ExposedWebPageEnrichmentNode(WebPageEnrichmentNode):
    """公开受保护方法，便于测试完整 HTML 抓取(boilerplate 净化)与增强写回。"""

    async def fetch_webpage(
        self,
        url: str,
        timeout_seconds: int,
        minimum_content_length: int = 200,
    ) -> dict:
        """调用节点网页抓取方法。"""
        return await self._fetch_webpage(url, timeout_seconds, minimum_content_length)

    async def fetch_html_clean_text(self, url: str, deadline: float) -> str | None:
        """调用节点完整 HTML 净化正文派生方法。"""
        return await self._fetch_html_clean_text(url, deadline)

    def apply_enrichment(self, doc_info: dict, evidence: WebPageEvidenceContent, fetched: dict) -> dict:
        """调用节点增强写回方法。"""
        return self._apply_enrichment(doc_info, evidence, fetched)


# ---------------------------------------------------------------------------
# framework 侧:完整 HTML 抓取与净化正文(mock 网络层)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_webpage_head_without_date_keeps_content():
    """<head> 提取不到日期时，正文抓取结果不受影响。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/plain"
    direct_result = {"url": url, "status_code": 200, "content": "x" * 300}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _HEAD_FETCH_TARGET,
        return_value=_HEAD_HTML_WITHOUT_DATE,
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["content"] == "x" * 300
    assert "doc_date" not in result


@pytest.mark.asyncio
async def test_fetch_webpage_head_fetch_failure_degrades_silently():
    """head 抓取超时/失败时应静默跳过，不影响已成功的正文抓取。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/slow"
    direct_result = {"url": url, "status_code": 200, "content": "x" * 300}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _HEAD_FETCH_TARGET,
        side_effect=requests.Timeout("head fetch timed out"),
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["content"] == "x" * 300
    assert result["fetch_method"] == "harness_webpage_fetch"
    assert "doc_date" not in result


@pytest.mark.asyncio
async def test_fetch_webpage_jina_path_skips_full_html_fetch():
    """Jina fallback 路径没有 HTML，不应触发完整 HTML 抓取。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/fallback"
    direct_result = {"url": url, "status_code": 200, "content": "short"}
    jina_result = {"url": url, "status_code": 200, "content": "y" * 300}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _JINA_FETCH_TARGET,
        return_value=jina_result,
    ), patch(_HEAD_FETCH_TARGET) as mock_head_fetch:
        result = await node.fetch_webpage(url, 45)

    assert result["fetch_method"] == "jina_reader"
    assert "doc_date" not in result
    mock_head_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_html_sidecar_caps_timeout_at_ten_seconds():
    """完整 HTML 抓取超时应取独立上限 10s 与剩余 deadline 的较小值。"""
    node = ExposedWebPageEnrichmentNode()
    deadline = asyncio.get_running_loop().time() + 45

    with patch(_HEAD_FETCH_TARGET, return_value=_HEAD_HTML_WITHOUT_DATE) as mock_head_fetch:
        await node.fetch_html_clean_text("https://a.com", deadline)

    assert mock_head_fetch.call_args.args[1] == 10


@pytest.mark.asyncio
async def test_fetch_html_sidecar_uses_remaining_deadline_when_shorter():
    """剩余 deadline 不足 10s 时，完整 HTML 抓取应使用剩余秒数。"""
    node = ExposedWebPageEnrichmentNode()
    deadline = asyncio.get_running_loop().time() + 3

    with patch(_HEAD_FETCH_TARGET, return_value=_HEAD_HTML_WITHOUT_DATE) as mock_head_fetch:
        await node.fetch_html_clean_text("https://a.com", deadline)

    assert mock_head_fetch.call_args.args[1] <= 3


def test_apply_enrichment_without_doc_date_keeps_doc_dates_untouched():
    """Jina 路径无 doc_date 时，写回不引入 date_info。"""
    node = ExposedWebPageEnrichmentNode()
    doc_info = {
        "doc_id": "d1",
        "source_id": "s1",
        "url": "https://a.com",
        "publish_time": "未提供时间信息",
        "original_content": "",
        "key_passages": [],
    }
    evidence = WebPageEvidenceContent(original_content="压缩后的正文证据", key_passages=["片段"])
    fetched = {"status_code": 200, "url": "https://a.com", "fetch_method": "jina_reader"}

    enriched = node.apply_enrichment(doc_info, evidence, fetched)

    assert "date_info" not in enriched
    assert enriched["publish_time"] == "未提供时间信息"
