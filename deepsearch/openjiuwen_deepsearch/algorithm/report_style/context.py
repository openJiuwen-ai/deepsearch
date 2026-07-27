# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Build the bounded semantic context used for report CSS generation."""

from __future__ import annotations

import re
from dataclasses import dataclass


HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^[ \t]*\|", re.MULTILINE)
IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
CITATION_RE = re.compile(r"\[\[\d+]]\(https?://[^)]+\)")
SUMMARY_HEADINGS = frozenset({"摘要", "abstract"})
FALLBACK_ABSTRACT_LIMIT = 4000


@dataclass(frozen=True, slots=True)
class StyleContext:
    """Describe report metadata available to the CSS-generation prompt.

    Attributes:
        title: 报告一级标题；没有标题时使用默认名称。
        headings: 原 Markdown 中按出现顺序提取的全部标题文本。
        abstract: 完整摘要，或无摘要时首个正文块的受限片段。
        table_count: Markdown 表格行数量，用于判断数据密度。
        image_count: Markdown 图片数量。
        citation_count: Markdown 外链引用数量。
    """

    title: str
    headings: tuple[str, ...]
    abstract: str
    table_count: int
    image_count: int
    citation_count: int

    def to_prompt_dict(self) -> dict:
        """Convert the context into template variables for the style prompt.

        Returns:
            dict: Jinja 模板所需的标题、摘要、统计值和允许选择器变量。
        """
        return {
            "report_title": self.title,
            "headings": "\n".join(f"- {heading}" for heading in self.headings),
            "abstract": self.abstract,
            "table_count": self.table_count,
            "image_count": self.image_count,
            "citation_count": self.citation_count,
        }


def _extract_abstract(markdown: str, heading_matches: list[re.Match[str]]) -> str:
    """Extract a complete summary section or the bounded first body block.

    Args:
        markdown: 原始 Markdown 报告内容。
        heading_matches: 已按文本位置排序的标题匹配结果。

    Returns:
        str: 完整摘要，或无摘要时最多 4,000 字符的首个正文块。
    """
    for index, match in enumerate(heading_matches):
        heading_text = match.group(2).strip().casefold()
        if heading_text not in SUMMARY_HEADINGS:
            continue
        level = len(match.group(1))
        content_start = match.end()
        content_end = len(markdown)
        for next_match in heading_matches[index + 1:]:
            if len(next_match.group(1)) <= level:
                content_end = next_match.start()
                break
        return markdown[content_start:content_end].strip()

    body = HEADING_RE.sub("", markdown).strip()
    first_block = re.split(r"\n\s*\n", body, maxsplit=1)[0].strip()
    return first_block[:FALLBACK_ABSTRACT_LIMIT]


def build_style_context(markdown: str) -> StyleContext:
    """Build the prompt context without sending an entire long report to the LLM.

    Args:
        markdown: 完整报告 Markdown 文本。

    Returns:
        StyleContext: 标题树、摘要及样式相关资源统计。
    """
    heading_matches = list(HEADING_RE.finditer(markdown))
    # 保留 # 层级，模型才能从扁平文本恢复章节父子关系。
    headings = tuple(f"{match.group(1)} {match.group(2).strip()}" for match in heading_matches)
    return StyleContext(
        title=heading_matches[0].group(2).strip() if heading_matches else "Report",
        headings=headings,
        abstract=_extract_abstract(markdown, heading_matches),
        table_count=len(TABLE_ROW_RE.findall(markdown)),
        image_count=len(IMAGE_RE.findall(markdown)),
        citation_count=len(CITATION_RE.findall(markdown)),
    )
