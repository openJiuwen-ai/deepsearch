# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re

_CITATION_PATTERN = re.compile(
    r'(?:\[\s*checked_citation:\s*\d+\s*\]\[\[\d+\]\]\((?:[^()]|\([^()]*\))*\)'
    r'|\[\[\d+\]\]\((?:[^()]|\([^()]*\))*\)'
    r'|\[\s*citation:\s*\d+\s*\])'
)
_INFERENCE_MARKER_PATTERN = re.compile(r'\[([^\]]+)\]\(#inference:(\d+)\)')


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
    removed_citation_ranges: set[tuple[int, int]] = set()
    removed_inference_ids: list[int] = []

    all_matches = []
    for match in _CITATION_PATTERN.finditer(text):
        m_start, m_end = match.start(), match.end()
        if m_start >= start and m_end <= end:
            all_matches.append(("citation", match))
    for match in _INFERENCE_MARKER_PATTERN.finditer(text):
        m_start, m_end = match.start(), match.end()
        if m_start >= start and m_end <= end:
            all_matches.append(("inference", match))
    all_matches.sort(key=lambda item: item[1].start())

    parts = []
    last_pos = 0
    for match_type, match in all_matches:
        m_start, m_end = match.start(), match.end()
        parts.append(text[last_pos:m_start])
        if match_type == "citation":
            removed_citation_ranges.add((m_start, m_end))
        else:
            parts.append(match.group(1))
            removed_inference_ids.append(int(match.group(2)))
        last_pos = m_end

    parts.append(text[last_pos:])
    return "".join(parts), removed_citation_ranges, removed_inference_ids
