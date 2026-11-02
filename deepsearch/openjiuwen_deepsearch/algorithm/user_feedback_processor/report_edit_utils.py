"""Clean ordinary citation markers within a report edit selection."""

import re
from dataclasses import dataclass

from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url

_LEGACY_LINK_CITATION_PREFIX_PATTERN = re.compile(r'\[\[\d+\]\]\(')
_PLAIN_CITATION_PATTERN = re.compile(r'\[\s*citation:\s*\d+\s*\]')


@dataclass(frozen=True)
class CitationMarker:
    """记录解析出的 citation 标记。

    Args:
        start: 标记起始偏移。
        end: 标记结束偏移。
        text: 标记原始文本。
    """

    start: int
    end: int
    text: str


def _iter_url_citation_markers(text: str, prefix_pattern: re.Pattern) -> list[CitationMarker]:
    """按前缀正则和 Markdown URL 扫描器解析 URL 型 citation。

    Args:
        text: 待解析文本。
        prefix_pattern: 匹配到 URL 左括号为止的 citation 前缀正则。

    Returns:
        URL 成功闭合的 citation 标记列表。
    """
    markers = []
    for match in prefix_pattern.finditer(text):
        open_paren_index = match.end() - 1
        parsed_url = extract_markdown_url(text, open_paren_index)
        if parsed_url is None:
            continue
        _, marker_end = parsed_url
        markers.append(CitationMarker(match.start(), marker_end, text[match.start():marker_end]))
    return markers


def _iter_citation_markers(text: str) -> list[CitationMarker]:
    """解析报告中的所有 citation 标记。

    Args:
        text: 待解析文本。

    Returns:
        CitationMarker 列表，包含链接引用和 ``[citation:n]``。
    """
    markers = [
        *_iter_url_citation_markers(text, _LEGACY_LINK_CITATION_PREFIX_PATTERN),
    ]
    markers.extend(
        CitationMarker(match.start(), match.end(), match.group(0))
        for match in _PLAIN_CITATION_PATTERN.finditer(text)
    )
    markers.sort(key=lambda marker: (marker.start, -marker.end))
    non_overlapping_markers = []
    cursor = -1
    for marker in markers:
        if marker.start < cursor:
            continue
        non_overlapping_markers.append(marker)
        cursor = marker.end
    return non_overlapping_markers


def strip_citations_in_range(text: str, start: int, end: int) -> str:
    """Remove complete citation markers inside the selection; preserve other text."""
    markers = [
        marker for marker in _iter_citation_markers(text)
        if start <= marker.start and marker.end <= end
    ]
    for marker in reversed(markers):
        text = text[:marker.start] + text[marker.end:]
    return text
