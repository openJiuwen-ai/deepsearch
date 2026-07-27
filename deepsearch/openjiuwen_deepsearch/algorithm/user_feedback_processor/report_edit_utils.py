# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from dataclasses import dataclass, field

from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url

_CHECKED_CITATION_PREFIX_PATTERN = re.compile(r'\[\s*checked_citation:\s*\d+\s*\]\[\[\d+\]\]\(')
_LEGACY_LINK_CITATION_PREFIX_PATTERN = re.compile(r'\[\[\d+\]\]\(')
_PLAIN_CITATION_PATTERN = re.compile(r'\[\s*citation:\s*\d+\s*\]')
_INFERENCE_MARKER_PATTERN = re.compile(r'\[([^\]]+)\]\(#inference:(\d+)\)')


@dataclass(frozen=True)
class RemovedCitation:
    """记录一次被清理的引用标记。

    Args:
        raw_start: 引用标记在原始报告中的起始偏移。
        raw_end: 引用标记在原始报告中的结束偏移。
        marker_text: 原始引用标记文本。
        checked_citation_id: checked citation 实例 id；非 checked 格式时为 None。
        plain_citation_index: ``[citation: n]`` 编号；非该格式时为 None。
    """

    raw_start: int
    raw_end: int
    marker_text: str
    checked_citation_id: int | None = None
    plain_citation_index: int | None = None


@dataclass(frozen=True)
class MarkupStripResult:
    """带可回溯信息的 markup 清洗结果。

    Args:
        text: 清洗指定范围后的完整报告文本。
        removed_citation_ranges: 被移除 citation 的原始偏移范围集合。
        removed_inference_ids: 被还原为纯文本的 inference id 列表。
        removed_citations: 被移除 citation 的结构化信息。
        clean_range_start: 清洗后目标范围在 ``text`` 中的起始偏移。
        clean_range_end: 清洗后目标范围在 ``text`` 中的结束偏移。
        clean_boundary_to_raw_boundary: 目标范围内 clean 文本边界到原始报告 raw 边界的映射。
    """

    text: str
    removed_citation_ranges: set[tuple[int, int]] = field(default_factory=set)
    removed_inference_ids: list[int] = field(default_factory=list)
    removed_citations: list[RemovedCitation] = field(default_factory=list)
    clean_range_start: int = 0
    clean_range_end: int = 0
    clean_boundary_to_raw_boundary: list[int] = field(default_factory=list)


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
        CitationMarker 列表，包含 checked citation、链接引用和 ``[citation:n]``。
    """
    markers = [
        *_iter_url_citation_markers(text, _CHECKED_CITATION_PREFIX_PATTERN),
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


def _parse_checked_citation_id(marker_text: str) -> int | None:
    """从 checked citation 标记中解析实例 id。

    Args:
        marker_text: 原始 citation 标记。

    Returns:
        解析成功时返回实例 id，否则返回 None。
    """
    match = re.search(r"\[checked_citation:\s*(\d+)\s*\]", marker_text)
    return int(match.group(1)) if match else None


def _parse_plain_citation_index(marker_text: str) -> int | None:
    """从 ``[citation:n]`` 标记中解析编号。

    Args:
        marker_text: 原始 citation 标记。

    Returns:
        解析成功时返回 citation 编号，否则返回 None。
    """
    match = re.search(r"\[\s*citation:\s*(\d+)\s*\]", marker_text)
    return int(match.group(1)) if match else None


def strip_markup_in_range(
    text: str,
    start: int,
    end: int,
) -> tuple[str, set[tuple[int, int]], list[int]]:
    """移除指定范围内的 citation 标记，并将 inference 标记还原为纯文本。

    Args:
        text: 原始报告文本。
        start: 选区起始偏移量。
        end: 选区结束偏移量。

    Returns:
        tuple[str, set[tuple[int, int]], list[int]]:
            - 剥离标记后的文本
            - 被移除的 citation 标记的偏移范围集合
            - 被移除的 inference 标记的 ID 列表
    """
    result = strip_markup_in_range_with_metadata(text, start, end)
    return result.text, result.removed_citation_ranges, result.removed_inference_ids


def strip_markup_in_range_with_metadata(
    text: str,
    start: int,
    end: int,
) -> MarkupStripResult:
    """移除指定范围内 markup，并返回可用于差异回填的结构化元数据。

    Args:
        text: 原始报告文本。
        start: 目标范围起始偏移。
        end: 目标范围结束偏移。

    Returns:
        MarkupStripResult: 清洗后的完整文本与目标范围 clean-to-raw 边界映射。
    """
    removed_citation_ranges: set[tuple[int, int]] = set()
    removed_citations: list[RemovedCitation] = []
    removed_inference_ids: list[int] = []

    all_matches = []
    for marker in _iter_citation_markers(text):
        m_start, m_end = marker.start, marker.end
        if m_start >= start and m_end <= end:
            all_matches.append(("citation", marker))
    for match in _INFERENCE_MARKER_PATTERN.finditer(text):
        m_start, m_end = match.start(), match.end()
        if m_start >= start and m_end <= end:
            all_matches.append(("inference", match))
    all_matches.sort(key=lambda item: item[1].start if isinstance(item[1], CitationMarker) else item[1].start())

    clean_parts: list[str] = []
    boundary_map = [start]
    raw_pos = start

    def append_raw_text(raw_start: int, raw_end: int) -> None:
        """追加 raw 文本片段，并同步维护 clean-to-raw 边界映射。

        Args:
            raw_start: raw 文本起始偏移。
            raw_end: raw 文本结束偏移。
        """
        chunk = text[raw_start:raw_end]
        clean_parts.append(chunk)
        for index in range(len(chunk)):
            boundary_map.append(raw_start + index + 1)

    for match_type, match in all_matches:
        if isinstance(match, CitationMarker):
            m_start, m_end = match.start, match.end
        else:
            m_start, m_end = match.start(), match.end()
        append_raw_text(raw_pos, m_start)
        if match_type == "citation":
            marker_text = match.text
            removed_citation_ranges.add((m_start, m_end))
            removed_citations.append(
                RemovedCitation(
                    raw_start=m_start,
                    raw_end=m_end,
                    marker_text=marker_text,
                    checked_citation_id=_parse_checked_citation_id(marker_text),
                    plain_citation_index=_parse_plain_citation_index(marker_text),
                )
            )
            # citation 被删除但语义附着在前一个 clean 边界上，回填未变化片段时应保留它。
            boundary_map[-1] = m_end
        else:
            display_text = match.group(1)
            display_raw_start = m_start + 1
            clean_parts.append(display_text)
            for index in range(len(display_text)):
                boundary_map.append(display_raw_start + index + 1)
            # 完整 inference 显示文本未变化时，应能回填整个 Markdown 链接标记。
            boundary_map[-1] = m_end
            removed_inference_ids.append(int(match.group(2)))
        raw_pos = m_end

    append_raw_text(raw_pos, end)
    clean_selected_text = "".join(clean_parts)
    stripped_text = text[:start] + clean_selected_text + text[end:]
    return MarkupStripResult(
        text=stripped_text,
        removed_citation_ranges=removed_citation_ranges,
        removed_inference_ids=removed_inference_ids,
        removed_citations=removed_citations,
        clean_range_start=start,
        clean_range_end=start + len(clean_selected_text),
        clean_boundary_to_raw_boundary=boundary_map,
    )
