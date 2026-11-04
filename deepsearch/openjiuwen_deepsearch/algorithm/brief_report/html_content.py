"""Brief HTML 报告的 Markdown、引用与章节内容处理。"""

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class BriefHtmlPreprocessResult:
    """预处理后的 Markdown。

    Attributes:
        cleaned_markdown: 报告 Markdown 文本。
    """

    cleaned_markdown: str


@dataclass
class BriefHtmlSectionChunk:
    """报告 markdown 按 ``## `` 拆分出的单个章节块。

    Attributes:
        section_id: 章节唯一标识。
        title: 章节标题。
        markdown: 章节对应的完整 Markdown 块。
    """

    section_id: str
    title: str
    markdown: str


_ENTRY_LINE_RE = re.compile(r"(?ms)^\[(?P<num>\d+)\]\.\s*\[(?P<title>.*?)\]\(")
_INLINE_CITATION_PREFIX_RE = re.compile(r"\[\[(?P<number>\d+)\]\]\(")
_CITATION_OPAQUE_TAGS = frozenset({"a", "style", "template"})
_H1_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_H2_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_FENCED_CODE_DELIMITER_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_SUMMARY_HEADINGS = frozenset({"核心摘要", "Executive Summary"})
_REFERENCES_HEADINGS = frozenset({"参考文章", "References"})


def _strip_reference_entry_lines(text: str) -> str:
    """移除文末参考文献条目行（由 Python 从引用注册表确定性渲染）。

    Args:
        text: 报告 Markdown 文本。

    Returns:
        移除参考文献条目行后的 Markdown 文本。
    """
    return "\n".join(line for line in text.splitlines() if not _ENTRY_LINE_RE.match(line))


def _split_h2_blocks(text: str) -> list[str]:
    """按二级标题拆分 Markdown，忽略 fenced code block 内的 ``## `` 行。

    Args:
        text: 待拆分的 Markdown 文本。

    Returns:
        按顶层二级标题切分出的 Markdown 块列表。
    """
    blocks: list[str] = []
    current: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        # 只有不在围栏内的 ## 行才是章节标题；围栏长度至少与开启围栏一致才关闭。
        if fence_char is None and line.startswith("## "):
            if current:
                blocks.append("".join(current))
            current = [line]
        else:
            current.append(line)

        match = _FENCED_CODE_DELIMITER_RE.match(line)
        if match is None:
            continue
        fence = match.group("fence")
        if fence_char is None:
            fence_char, fence_length = fence[0], len(fence)
        elif fence[0] == fence_char and len(fence) >= fence_length:
            fence_char, fence_length = None, 0
    if current:
        blocks.append("".join(current))
    return blocks


def split_report_markdown(cleaned: str) -> tuple[str, str, list[BriefHtmlSectionChunk]]:
    """把清洗后的报告 markdown 拆为标题、摘要与章节块。

    Args:
        cleaned: 已完成引用清理的 Markdown 文本。

    Returns:
        三元组，依次为报告标题、摘要 Markdown 和章节块列表。
    """
    text = _strip_reference_entry_lines(cleaned)
    title_match = _H1_TITLE_RE.search(text)
    title = title_match.group(1) if title_match else ""
    summary_md = ""
    sections: list[BriefHtmlSectionChunk] = []
    for part in _split_h2_blocks(text):
        if not part.strip():
            continue
        heading_match = _H2_HEADING_RE.match(part)
        if heading_match is None:
            continue
        heading = heading_match.group(1).strip()
        if heading in _SUMMARY_HEADINGS:
            summary_md = part
            continue
        if heading in _REFERENCES_HEADINGS:
            continue
        section_id = heading.split(" ", 1)[0] if " " in heading else str(len(sections) + 1)
        sections.append(
            BriefHtmlSectionChunk(
                section_id=section_id,
                title=heading.split(" ", 1)[1] if " " in heading else heading,
                markdown=part,
            )
        )
    return title, summary_md, sections


def preprocess_markdown(markdown: str) -> BriefHtmlPreprocessResult:
    """Prepare report Markdown without generating references."""
    return BriefHtmlPreprocessResult(cleaned_markdown=markdown)
