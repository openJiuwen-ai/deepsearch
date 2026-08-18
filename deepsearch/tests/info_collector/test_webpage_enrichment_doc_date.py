import asyncio
from unittest.mock import Mock, patch

import pytest
import requests

from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import (
    WebPageEvidenceContent,
    merge_fetched_doc_date,
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

_HEAD_HTML_WITH_META_DATE = """
<html><head>
<title>Some article</title>
<meta property="article:published_time" content="2024-03-15T08:00:00Z" />
<meta name="viewport" content="width=device-width" />
</head><body><p>body</p></body></html>
"""

_HEAD_HTML_WITH_JSONLD_DATE = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle",
 "headline": "x", "datePublished": "2024-05-01"}
</script>
</head><body><p>body</p></body></html>
"""

_HEAD_HTML_WITHOUT_DATE = """
<html><head>
<title>No date here</title>
<meta name="description" content="plain page" />
</head><body><p>body</p></body></html>
"""


class ExposedWebPageEnrichmentNode(WebPageEnrichmentNode):
    """公开受保护方法，便于测试 head 日期抓取与写回逻辑。"""

    async def fetch_webpage(
        self,
        url: str,
        timeout_seconds: int,
        minimum_content_length: int = 200,
    ) -> dict:
        """调用节点网页抓取方法。"""
        return await self._fetch_webpage(url, timeout_seconds, minimum_content_length)

    async def fetch_html_date_and_clean_text(self, url: str, deadline: float) -> tuple[dict | None, str | None]:
        """调用节点完整 HTML 日期与净化正文派生方法。"""
        return await self._fetch_html_date_and_clean_text(url, deadline)

    def apply_enrichment(self, doc_info: dict, evidence: WebPageEvidenceContent, fetched: dict) -> dict:
        """调用节点增强写回方法。"""
        return self._apply_enrichment(doc_info, evidence, fetched)


# ---------------------------------------------------------------------------
# algorithm 侧:merge_fetched_doc_date
# ---------------------------------------------------------------------------


def test_merge_fetched_doc_date_sets_date_info_and_unknown_publish_time():
    """抓取到 head 日期且 publish_time 未知时，应写回 date_info 并更新展示字段。"""
    doc_info = {"doc_id": "d1", "url": "https://a.com", "publish_time": "未提供时间信息"}
    fetched = {
        "doc_date": {
            "date": "2024-03-15",
            "granularity": "day",
            "confidence": "high",
            "source": "html_meta:article:published_time",
        }
    }

    updated = merge_fetched_doc_date(doc_info, fetched)

    assert updated["date_info"] == fetched["doc_date"]
    assert updated["publish_time"] == "2024-03-15"
    assert "date_info" not in doc_info


def test_merge_fetched_doc_date_keeps_existing_publish_time():
    """publish_time 已有值时只写 date_info，不覆盖展示字段。"""
    doc_info = {"doc_id": "d1", "publish_time": "2024-01-01"}
    fetched = {
        "doc_date": {
            "date": "2024-03-15",
            "granularity": "day",
            "confidence": "high",
            "source": "html_meta:article:published_time",
        }
    }

    updated = merge_fetched_doc_date(doc_info, fetched)

    assert updated["date_info"]["date"] == "2024-03-15"
    assert updated["publish_time"] == "2024-01-01"


def test_merge_fetched_doc_date_without_doc_date_keeps_doc_untouched():
    """抓取结果无 head 日期时文档保持原样。"""
    doc_info = {"doc_id": "d1", "publish_time": "未提供时间信息"}

    updated = merge_fetched_doc_date(doc_info, {"url": "https://a.com"})

    assert updated is doc_info


def test_merge_fetched_doc_date_ignores_invalid_doc_date():
    """doc_date 结构非法时按无日期处理。"""
    doc_info = {"doc_id": "d1", "publish_time": "未提供时间信息"}

    updated = merge_fetched_doc_date(doc_info, {"doc_date": {"date": "not-a-date", "confidence": "high"}})

    assert updated is doc_info


def test_merge_fetched_doc_date_conflict_with_engine_metadata_degrades_to_unknown():
    """head 日期与引擎元数据同为 high 且矛盾时，date_info 降级为 unknown。"""
    doc_info = {
        "doc_id": "d1",
        "publish_time": "2024-01-10",
        "date_metadata": {"field": "source_date", "type": "published", "parsed_date": "2024-01-10"},
    }
    fetched = {
        "doc_date": {
            "date": "2025-03-02",
            "granularity": "day",
            "confidence": "high",
            "source": "html_meta:article:published_time",
        }
    }

    updated = merge_fetched_doc_date(doc_info, fetched)

    assert "date_info" not in updated
    assert updated["publish_time"] == "2024-01-10"


def test_merge_fetched_doc_date_agrees_with_engine_metadata():
    """head 日期与引擎元数据一致时，合并保留 high 置信日期。"""
    doc_info = {
        "doc_id": "d1",
        "publish_time": "2024-01-10",
        "date_metadata": {"field": "source_date", "type": "published", "parsed_date": "2024-01-10"},
    }
    fetched = {
        "doc_date": {
            "date": "2024-01-10",
            "granularity": "day",
            "confidence": "high",
            "source": "html_meta:article:published_time",
        }
    }

    updated = merge_fetched_doc_date(doc_info, fetched)

    assert updated["date_info"]["date"] == "2024-01-10"
    assert updated["date_info"]["confidence"] == "high"


def test_merge_fetched_doc_date_high_beats_existing_medium_date_info():
    """已有 medium 置信 date_info 时，high 置信的 head 日期应胜出。"""
    doc_info = {
        "doc_id": "d1",
        "publish_time": "2024-03-01",
        "date_info": {"date": "2024-03-01", "granularity": "month", "confidence": "medium", "source": "url"},
    }
    fetched = {
        "doc_date": {
            "date": "2024-03-15",
            "granularity": "day",
            "confidence": "high",
            "source": "html_meta:article:published_time",
        }
    }

    updated = merge_fetched_doc_date(doc_info, fetched)

    assert updated["date_info"]["date"] == "2024-03-15"
    assert updated["date_info"]["granularity"] == "day"


# ---------------------------------------------------------------------------
# framework 侧:head 日期抓取(mock 网络层)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_webpage_direct_success_extracts_head_meta_date():
    """直连抓取成功后，应顺带从 <head> 白名单 meta 提取发布日期。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/article"
    direct_result = {"url": url, "status_code": 200, "content": "x" * 300}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _HEAD_FETCH_TARGET,
        return_value=_HEAD_HTML_WITH_META_DATE,
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["fetch_method"] == "harness_webpage_fetch"
    assert result["doc_date"] == {
        "date": "2024-03-15",
        "granularity": "day",
        "confidence": "high",
        "source": "html_meta:article:published_time",
    }


@pytest.mark.asyncio
async def test_fetch_webpage_direct_success_extracts_jsonld_date():
    """<head> 无白名单 meta 时，应提取 JSON-LD 主实体的 datePublished。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/news"
    direct_result = {"url": url, "status_code": 200, "content": "x" * 300}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _HEAD_FETCH_TARGET,
        return_value=_HEAD_HTML_WITH_JSONLD_DATE,
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["doc_date"]["date"] == "2024-05-01"
    assert result["doc_date"]["source"] == "jsonld:published"


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
async def test_fetch_webpage_jina_path_skips_head_date_fetch():
    """Jina fallback 路径没有 HTML，不应触发 head 日期抓取。"""
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
        doc_date, _ = await node.fetch_html_date_and_clean_text("https://a.com", deadline)

    assert doc_date is None
    assert mock_head_fetch.call_args.args[1] == 10


@pytest.mark.asyncio
async def test_fetch_html_sidecar_uses_remaining_deadline_when_shorter():
    """剩余 deadline 不足 10s 时，完整 HTML 抓取应使用剩余秒数。"""
    node = ExposedWebPageEnrichmentNode()
    deadline = asyncio.get_running_loop().time() + 3

    with patch(_HEAD_FETCH_TARGET, return_value=_HEAD_HTML_WITHOUT_DATE) as mock_head_fetch:
        await node.fetch_html_date_and_clean_text("https://a.com", deadline)

    assert mock_head_fetch.call_args.args[1] <= 3


def test_apply_enrichment_writes_doc_date_into_doc():
    """增强写回应顺带合并 head 日期到 date_info。"""
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
    fetched = {
        "status_code": 200,
        "url": "https://a.com",
        "fetch_method": "harness_webpage_fetch",
        "doc_date": {
            "date": "2024-03-15",
            "granularity": "day",
            "confidence": "high",
            "source": "html_meta:article:published_time",
        },
    }

    enriched = node.apply_enrichment(doc_info, evidence, fetched)

    assert enriched["original_content"] == "压缩后的正文证据"
    assert enriched["date_info"]["date"] == "2024-03-15"
    assert enriched["publish_time"] == "2024-03-15"


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
