"""Brief 章节 Markdown 的引用和标题确定性校验。"""

import re

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefSection

CITATION_PATTERN = re.compile(r"\[\s*citation:\s*(\d+)\s*\]")


def _normalize_headings(markdown: str, section: BriefSection) -> str:
    """规范化章节标题层级，并保留 fenced code 内的原始文本。"""
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    has_section_heading = False
    for line in markdown.splitlines():
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            normalized.append(line)
        elif in_fence:
            normalized.append(line)
        else:
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if not match:
                normalized.append(line)
            elif len(match.group(1)) == 1:
                if not has_section_heading:
                    normalized.append(f"## {section.id} {section.title}")
                    has_section_heading = True
            else:
                normalized.append(f"### {match.group(2)}")
    if not has_section_heading:
        normalized.insert(0, f"## {section.id} {section.title}")
    return "\n".join(normalized).strip()


def sanitize_brief_chapter(
    markdown: str,
    section: BriefSection,
    allowed_citations: set[int],
) -> str:
    """规范化章节边界，并删除无效引用。

    Args:
        markdown: LLM 输出的原始章节 Markdown。
        section: 当前章节的结构化合同。
        allowed_citations: 本章可使用的引用编号。

    Returns:
        保留原有正文、仅进行引用和边界清理后的章节 Markdown。
    """
    normalized = _normalize_headings(markdown, section)
    normalized = CITATION_PATTERN.sub(
        lambda match: match.group(0) if int(match.group(1)) in allowed_citations else "",
        normalized,
    )
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()
