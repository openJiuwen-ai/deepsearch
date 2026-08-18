# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""date_utils 的单元测试:解析、区间判定、URL/HTML 提取、多源合并。"""

from datetime import date

import pytest

from openjiuwen_deepsearch.utils.common_utils.date_utils import (
    DocDate,
    extract_html_head_date,
    extract_url_date,
    is_plausible,
    merge_doc_dates,
    parse_date_string,
    parse_partial_date,
    temporal_status,
    timeliness_score,
    to_interval,
)

REF = date(2026, 8, 17)  # 固定参考日期,避免测试随时间漂移


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tue, 11 Mar 2025 17:00:00 GMT", date(2025, 3, 11)),
        ("Tue, 11 Mar 2025 01:00:00 +0800", date(2025, 3, 10)),
        ("2024-01-02", date(2024, 1, 2)),
        ("2024-01-02T03:04:05Z", date(2024, 1, 2)),
        ("2024-01-02T01:04:05+08:00", date(2024, 1, 1)),
        ("Jan 1, 2024", date(2024, 1, 1)),
        ("2024年1月1日", date(2024, 1, 1)),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_string(raw, expected):
    assert parse_date_string(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-03-15", (date(2024, 3, 15), "day")),
        ("2024-03", (date(2024, 3, 1), "month")),
        ("2024年3月", (date(2024, 3, 1), "month")),
        ("March 2024", (date(2024, 3, 1), "month")),
        ("2024", (date(2024, 1, 1), "year")),
        ("2024 Jan", (date(2024, 1, 1), "month")),
        ("garbage", None),
    ],
)
def test_parse_partial_date(raw, expected):
    assert parse_partial_date(raw) == expected


def test_to_interval_expands_by_granularity():
    assert to_interval(date(2024, 3, 15), "day") == (date(2024, 3, 15), date(2024, 3, 15))
    assert to_interval(date(2024, 3, 15), "month") == (date(2024, 3, 1), date(2024, 3, 31))
    assert to_interval(date(2024, 3, 15), "year") == (date(2024, 1, 1), date(2024, 12, 31))
    # 二月闰年
    assert to_interval(date(2024, 2, 10), "month") == (date(2024, 2, 1), date(2024, 2, 29))


# ---------------------------------------------------------------------------
# 合理性校验
# ---------------------------------------------------------------------------

def test_is_plausible_rejects_future_and_ancient():
    future = DocDate(day=date(2027, 1, 1), granularity="year", confidence="high")
    assert not is_plausible(future, REF)
    # 容忍窗口内(REF+2 天)的未来日期放行
    near = DocDate(day=date(2026, 8, 18), granularity="day", confidence="high")
    assert is_plausible(near, REF)
    ancient = DocDate(day=date(1985, 1, 1), granularity="year", confidence="high")
    assert not is_plausible(ancient, REF)


# ---------------------------------------------------------------------------
# 时效状态(区间包含)
# ---------------------------------------------------------------------------

def _dd(year, month=1, day=1, granularity="day", confidence="high"):
    return DocDate(day=date(year, month, day), granularity=granularity, confidence=confidence)


@pytest.mark.parametrize(
    ("doc", "start", "end", "expected"),
    [
        # 日粒度,明确在范围内/外
        (_dd(2024, 3, 15), date(2024, 1, 1), date(2024, 12, 31), "compliant"),
        (_dd(2023, 12, 31), date(2024, 1, 1), None, "violation"),
        (_dd(2025, 1, 1), None, date(2024, 12, 31), "violation"),
        # 年粒度跨边界 → unknown(粒度不足,不猜)
        (_dd(2024, 1, 1, granularity="year"), date(2024, 6, 1), None, "unknown"),
        # 年粒度完整落入 → compliant
        (_dd(2024, 1, 1, granularity="year"), date(2024, 1, 1), date(2024, 12, 31), "compliant"),
        # 月粒度完整落入
        (_dd(2024, 8, 1, granularity="month"), date(2024, 6, 1), None, "compliant"),
        # 月粒度跨边界 → unknown
        (_dd(2024, 6, 1, granularity="month"), date(2024, 6, 15), None, "unknown"),
        # 无日期或无约束 → unknown
        (None, date(2024, 1, 1), None, "unknown"),
        (_dd(2024, 3, 15), None, None, "unknown"),
    ],
)
def test_temporal_status(doc, start, end, expected):
    assert temporal_status(doc, start, end) == expected


@pytest.mark.parametrize(
    ("status", "confidence", "expected"),
    [
        ("unknown", "high", 0.0),
        ("unknown", "low", 0.0),
        ("compliant", "high", 1.0),
        ("compliant", "medium", 0.5),
        ("violation", "medium", -0.5),
        ("violation", "low", -0.5),
        ("violation", "high", -1.0),
    ],
)
def test_timeliness_score(status, confidence, expected):
    assert timeliness_score(status, confidence) == expected


# ---------------------------------------------------------------------------
# URL 日期提取
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("url", "expected_day", "expected_granularity"),
    [
        ("https://example.com/2024/03/15/some-article", date(2024, 3, 15), "day"),
        ("https://example.com/2024-03-15-news.html", date(2024, 3, 15), "day"),
        ("https://example.com/20240315/article", date(2024, 3, 15), "day"),
        ("https://example.com/2024/03/article", date(2024, 3, 1), "month"),
        ("https://example.com/news/article-12345", None, None),  # 无日期
        ("https://example.com/best-laptops-2024", None, None),   # 主题年份,不匹配路径段模式
        ("https://example.com/2099/01/01/future", None, None),   # 未来日期被拒绝
    ],
)
def test_extract_url_date(url, expected_day, expected_granularity):
    result = extract_url_date(url, reference_date=REF)
    if expected_day is None:
        assert result is None
    else:
        assert result is not None
        assert result.day == expected_day
        assert result.granularity == expected_granularity
        assert result.confidence == "medium"


# ---------------------------------------------------------------------------
# HTML <head> 白名单提取
# ---------------------------------------------------------------------------

def test_extract_html_head_date_whitelist_meta():
    html = """
    <html><head>
      <meta property="article:published_time" content="2024-03-15T08:00:00Z">
      <meta name="keywords" content="2024-01-01 irrelevant">
    </head><body>2025年12月31日 侧边栏日期不应被收</body></html>
    """
    result = extract_html_head_date(html, reference_date=REF)
    assert result is not None
    assert result.day == date(2024, 3, 15)
    assert result.confidence == "high"
    assert "article:published_time" in result.source


def test_extract_html_head_date_jsonld_main_entity_only():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"NewsArticle","datePublished":"2024-05-01",
     "comment":{"@type":"Comment","datePublished":"2020-01-01"}}
    </script>
    </head></html>
    """
    result = extract_html_head_date(html, reference_date=REF)
    assert result is not None
    assert result.day == date(2024, 5, 1)


def test_extract_html_head_date_published_beats_modified():
    html = """
    <html><head>
      <meta property="article:published_time" content="2024-03-15T08:00:00Z">
      <meta property="article:modified_time" content="2025-01-01T08:00:00Z">
    </head></html>
    """
    result = extract_html_head_date(html, reference_date=REF)
    assert result is not None
    assert result.day == date(2024, 3, 15)


def test_extract_html_head_date_conflict_returns_none():
    html = """
    <html><head>
      <meta property="article:published_time" content="2024-03-15T08:00:00Z">
      <meta name="citation_publication_date" content="2021-06-01">
    </head></html>
    """
    assert extract_html_head_date(html, reference_date=REF) is None


def test_extract_html_head_date_rejects_future():
    html = """
    <html><head>
      <meta property="article:published_time" content="2099-01-01T00:00:00Z">
    </head></html>
    """
    assert extract_html_head_date(html, reference_date=REF) is None


# ---------------------------------------------------------------------------
# 多来源合并
# ---------------------------------------------------------------------------

def test_merge_prefers_high_confidence():
    high = _dd(2024, 3, 15, confidence="high")
    medium = _dd(2024, 1, 1, granularity="year", confidence="medium")
    assert merge_doc_dates([medium, high]) is high


def test_merge_same_tier_conflict_returns_none():
    a = _dd(2024, 3, 15, confidence="medium")
    b = _dd(2025, 1, 1, confidence="medium")
    assert merge_doc_dates([a, b]) is None


def test_merge_granularity_internal_difference_ok():
    # 同为 medium,一个日粒度一个年粒度但同年 → 不矛盾
    a = DocDate(day=date(2024, 3, 15), granularity="day", confidence="medium")
    b = DocDate(day=date(2024, 1, 1), granularity="year", confidence="medium")
    assert merge_doc_dates([a, b]) is a


def test_merge_empty():
    assert merge_doc_dates([None, None]) is None
