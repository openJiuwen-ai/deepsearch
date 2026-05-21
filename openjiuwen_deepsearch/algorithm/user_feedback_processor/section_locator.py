# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from dataclasses import dataclass

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$", re.MULTILINE)
_NUMBERED_MAJOR_TITLE_PATTERN = re.compile(r"^(?P<number>\d+)\.\s*")


@dataclass(frozen=True)
class LocatedSection:
    """报告内由 Markdown 标题划定的一块连续区间及其元数据。"""

    section_start_offset: int
    section_end_offset: int
    section_text: str
    section_heading: str


@dataclass(frozen=True)
class LocatedHeadingBlock:
    """Markdown 标题块定位结果。

    Attributes:
        start (int): 标题行起始偏移。
        end (int): 标题行结束偏移。
        level (int): Markdown 标题层级。
        title (str): 去掉 ``#`` 前缀后的标题文本。
        heading (str): 完整标题行文本。
        block_start (int): 标题块起始偏移。
        block_end (int): 标题块结束偏移。
    """

    start: int
    end: int
    level: int
    title: str
    heading: str
    block_start: int
    block_end: int


def locate_section(report: str, start_offset: int, end_offset: int) -> LocatedSection:
    """定位包含 ``[start_offset, end_offset)`` 的最小标题区块。

    Args:
        report (str): 完整报告正文。
        start_offset (int): 选区起始偏移。
        end_offset (int): 选区结束偏移。

    Returns:
        LocatedSection: 最小标题区块；无标题时退回整篇报告。
    """
    headings = parse_markdown_headings(report)
    if headings:
        return _locate_smallest_enclosing_heading_block(report, start_offset, end_offset, headings)
    return LocatedSection(
        section_start_offset=0,
        section_end_offset=len(report),
        section_text=report,
        section_heading="",
    )


def parse_markdown_headings(report: str) -> list[dict]:
    """解析报告中的 Markdown 标题行。

    Args:
        report (str): 完整报告正文。

    Returns:
        list[dict]: 标题信息列表，包含 start、end、level、title、heading 字段。
    """
    return [
        {
            "start": match.start(),
            "end": match.end(),
            "level": len(match.group(1)),
            "title": match.group(2).strip(),
            "heading": match.group(0),
        }
        for match in _HEADING_PATTERN.finditer(report or "")
    ]


def heading_block_end(report: str, headings: list[dict], heading_index: int) -> int:
    """计算某个标题块在原报告中的结束偏移。

    Args:
        report (str): 完整报告正文。
        headings (list[dict]): 已解析的标题列表。
        heading_index (int): 目标标题在 ``headings`` 中的索引。

    Returns:
        int: 标题块结束偏移。
    """
    heading = headings[heading_index]
    for next_heading in headings[heading_index + 1:]:
        if next_heading["level"] <= heading["level"]:
            return next_heading["start"]
    return len(report)


def locate_enclosing_numbered_major_block(
    report: str,
    start_offset: int,
    end_offset: int,
) -> LocatedHeadingBlock | None:
    """查找包含选区的一级数字编号大章节块。

    Args:
        report (str): 完整报告正文。
        start_offset (int): 选区起始偏移。
        end_offset (int): 选区结束偏移。

    Returns:
        LocatedHeadingBlock | None: 命中的编号大章节块；未命中时返回 ``None``。
    """
    headings = parse_markdown_headings(report)
    for index, heading in enumerate(headings):
        if heading["level"] != 1 or not _NUMBERED_MAJOR_TITLE_PATTERN.match(heading["title"]):
            continue
        block_end = heading_block_end(report, headings, index)
        if start_offset >= heading["start"] and end_offset <= block_end:
            return LocatedHeadingBlock(
                start=heading["start"],
                end=heading["end"],
                level=heading["level"],
                title=heading["title"],
                heading=heading["heading"],
                block_start=heading["start"],
                block_end=block_end,
            )
    return None


def _locate_smallest_enclosing_heading_block(
    report: str,
    start_offset: int,
    end_offset: int,
    headings: list[dict],
) -> LocatedSection:
    """在已解析的 ``headings`` 列表上选取能包住选区、span 最小且标题层级尽量深的一块。

    Args:
        report: 报告正文。
        start_offset: 选区起始偏移量。
        end_offset: 选区结束偏移量。
        headings: 已解析的标题列表，每个元素包含 start、end、level、heading 字段。

    Returns:
        LocatedSection: 最小包围标题区块信息；无匹配时返回整篇报告。
    """
    candidates = []
    for index, heading in enumerate(headings):
        block_start = heading["start"]
        block_end = heading_block_end(report, headings, index)
        if start_offset >= block_start and end_offset <= block_end:
            candidates.append(
                {
                    "start": block_start,
                    "end": block_end,
                    "heading": heading["heading"],
                    "span": block_end - block_start,
                    "level": heading["level"],
                }
            )

    if not candidates:
        return LocatedSection(
            section_start_offset=0,
            section_end_offset=len(report),
            section_text=report,
            section_heading="",
        )

    best = min(candidates, key=lambda item: (item["span"], -item["level"], -item["start"]))
    return LocatedSection(
        section_start_offset=best["start"],
        section_end_offset=best["end"],
        section_text=report[best["start"]:best["end"]],
        section_heading=best["heading"],
    )
