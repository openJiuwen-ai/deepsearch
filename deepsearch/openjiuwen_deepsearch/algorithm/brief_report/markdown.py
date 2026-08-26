"""Brief 章节 Markdown 的引用、标题和 Mermaid 确定性校验。"""

import re

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefSection

CITATION_PATTERN = re.compile(r"\[\s*citation:\s*(\d+)\s*\]")
MERMAID_BLOCK_PATTERN = re.compile(
    r"(?ms)(?P<caption>^\*\*(?:图：|Figure:\s*)[^\n]+\*\*[^\n]*\n)?"
    r"```mermaid\s*\n(?P<code>.*?)\n```"
)
MERMAID_ALLOWED_START = re.compile(
    r"^(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b|^sequenceDiagram$|"
    r"^stateDiagram(?:-v2)?$|^xychart-beta$|^pie\b|^timeline$",
    re.MULTILINE,
)
MERMAID_FORBIDDEN = re.compile(
    r"(?i)javascript:|<script|<iframe|click\s+|%%\{init|linkStyle|"
    r"classDef[^\n]*(?:url|href)"
)
MAX_MERMAID_CHARS = 6000
MAX_MERMAID_NODES = 80


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


def _citation_ids(text: str) -> set[int]:
    """提取 Markdown 中的 citation ID。"""
    return {int(match.group(1)) for match in CITATION_PATTERN.finditer(text)}


def _numbers(text: str) -> set[str]:
    """提取可被图表引入的数字字面量。"""
    return set(re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?%?", text))


def _node_count(code: str) -> int:
    """估算 Mermaid 节点数量以限制渲染成本。"""
    return len(set(re.findall(r"(?m)^\s*([A-Za-z][A-Za-z0-9_]*)\s*(?:\[|\(|\{|-->|---|:)", code)))


def _valid_mermaid(match: re.Match[str], allowed: set[int], support_text: str) -> bool:
    """验证图题引用、安全语法、数量和数值可追溯性。"""
    caption, code = match.group("caption") or "", match.group("code").strip()
    return bool(
        caption and _citation_ids(caption) and _citation_ids(caption) <= allowed
        and len(code) <= MAX_MERMAID_CHARS and _node_count(code) <= MAX_MERMAID_NODES
        and MERMAID_ALLOWED_START.search(code) and not MERMAID_FORBIDDEN.search(code)
        and _numbers(code) <= _numbers(support_text)
    )


def _sanitize_mermaid(markdown: str, allowed: set[int], evidence_text: str) -> str:
    """保留至多一张通过验证的 Mermaid，失败时连同图题删除。"""
    matches = list(MERMAID_BLOCK_PATTERN.finditer(markdown))
    kept, parts, cursor = False, [], 0
    support_text = f"{MERMAID_BLOCK_PATTERN.sub('', markdown)}\n{evidence_text}"
    for match in matches:
        parts.append(markdown[cursor:match.start()])
        if not kept and _valid_mermaid(match, allowed, support_text):
            parts.append(match.group(0))
            kept = True
        cursor = match.end()
    parts.append(markdown[cursor:])
    return "".join(parts)


def sanitize_brief_chapter(
    markdown: str,
    section: BriefSection,
    allowed_citations: set[int],
    evidence_text: str,
) -> str:
    """规范化章节边界，并删除无效引用和不可信 Mermaid。

    Args:
        markdown: LLM 输出的原始章节 Markdown。
        section: 当前章节的结构化合同。
        allowed_citations: 本章可使用的引用编号。
        evidence_text: 本章写作模型实际看到的证据摘要。

    Returns:
        保留原有正文、仅进行安全和边界清理后的章节 Markdown。
    """
    normalized = _normalize_headings(markdown, section)
    normalized = CITATION_PATTERN.sub(
        lambda match: match.group(0) if int(match.group(1)) in allowed_citations else "",
        normalized,
    )
    return re.sub(r"\n{3,}", "\n\n", _sanitize_mermaid(normalized, allowed_citations, evidence_text)).strip()
