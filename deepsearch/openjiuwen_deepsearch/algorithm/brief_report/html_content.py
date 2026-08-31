"""Brief HTML 报告的 Markdown、引用与章节内容处理。"""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url


@dataclass
class BriefHtmlPreprocessResult:
    """预处理清洗后的 markdown 与引用元数据。"""

    cleaned_markdown: str
    reference_entries: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class BriefHtmlSectionChunk:
    """报告 markdown 按 ``## `` 拆分出的单个章节块。"""

    section_id: str
    title: str
    markdown: str


_CHECKED_CITATION_RE = re.compile(r"\[checked_citation:[^\]]*\]\[\[(?P<num>\d+)\]\]\(")
_SOURCE_TRACER_RE = re.compile(
    r"(?P<image>!)?\[source_tracer_result\]\[(?P<title>.*?)\]\(", re.DOTALL
)
_ENTRY_LINE_RE = re.compile(r"(?ms)^\[(?P<num>\d+)\]\.\s*\[(?P<title>.*?)\]\(")
_INLINE_CITATION_PREFIX_RE = re.compile(r"\[\[(?P<number>\d+)\]\]\(")
_CITATION_OPAQUE_TAGS = frozenset({"a", "style", "template"})
_H1_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_H2_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_FENCED_CODE_DELIMITER_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_SUMMARY_HEADINGS = frozenset({"核心摘要", "Executive Summary"})
_REFERENCES_HEADINGS = frozenset({"参考文章", "References"})


def _reference_spans(markdown: str) -> list[tuple[int, re.Match, int | None, str]]:
    """按出现位置产出 checked 与 source_tracer 行内引用标记。"""
    spans: list[tuple[int, re.Match, int | None, str]] = []
    for match in _CHECKED_CITATION_RE.finditer(markdown):
        spans.append((match.start(), match, int(match.group("num")), ""))
    for match in _SOURCE_TRACER_RE.finditer(markdown):
        spans.append((match.start(), match, None, match.group("title")))
    spans.sort(key=lambda item: item[0])
    return spans


def preprocess_markdown(markdown: str) -> BriefHtmlPreprocessResult:
    """把行内引用标记清洗为 ``[[n]](URL)``，并规范化文末参考文献条目。"""
    entries: dict[int, tuple[str, str]] = {}
    url_to_number: dict[str, int] = {}
    for line_match in _ENTRY_LINE_RE.finditer(markdown):
        parsed = extract_markdown_url(markdown, line_match.end() - 1)
        if parsed is None:
            continue
        url, _end = parsed
        number = int(line_match.group("num"))
        entries[number] = (line_match.group("title"), url)
        url_to_number.setdefault(url, number)

    next_number = (max(entries) + 1) if entries else 1
    collected: dict[int, tuple[str, str]] = {}
    parts: list[str] = []
    cursor = 0
    for start, match, fixed_number, title in _reference_spans(markdown):
        parsed = extract_markdown_url(markdown, match.end() - 1)
        if parsed is None:
            continue
        url, end = parsed
        if fixed_number is not None:
            number = fixed_number
        elif url in url_to_number:
            number = url_to_number[url]
        else:
            number = next_number
            next_number += 1
        url_to_number.setdefault(url, number)
        if number not in entries and number not in collected:
            collected[number] = (title or url, url)
        parts.append(markdown[cursor:start])
        parts.append(f"[[{number}]]({url})")
        cursor = end
    parts.append(markdown[cursor:])
    cleaned = "".join(parts)

    merged = {**entries, **collected}
    missing = {number: item for number, item in collected.items() if number not in entries}
    if missing:
        lines = [cleaned.rstrip("\n")]
        lines.extend(
            f"[{number}]. [{missing[number][0]}]({missing[number][1]})"
            for number in sorted(missing)
        )
        cleaned = "\n".join(lines)

    return BriefHtmlPreprocessResult(
        cleaned_markdown=cleaned,
        reference_entries=[(n, merged[n][0], merged[n][1]) for n in sorted(merged)],
    )


def _citation_urls(pre: BriefHtmlPreprocessResult) -> dict[int, str]:
    """提取可确定映射的引用目标，供拼装层转写。"""
    return {
        number: url
        for number, _title, url in pre.reference_entries
        if isinstance(url, str) and url
    }


def _render_inline_citation_text(text: str, citation_urls: dict[int, str]) -> str:
    """把文本节点中的合法 Markdown 引用标记转成安全的上标引用。"""
    output: list[str] = []
    cursor = 0
    search_from = 0
    while True:
        match = _INLINE_CITATION_PREFIX_RE.search(text, search_from)
        if match is None:
            output.append(html.escape(text[cursor:], quote=False))
            break
        number = int(match.group("number"))
        parsed = extract_markdown_url(text, match.end() - 1)
        if parsed is None:
            search_from = match.end()
            continue
        url, end = parsed
        if citation_urls.get(number) != url:
            search_from = match.end()
            continue
        output.append(html.escape(text[cursor:match.start()], quote=False))
        if url.lower().startswith(("http://", "https://")):
            escaped_url = html.escape(url, quote=True)
            output.append(
                f'<sup class="cite-ref"><a href="{escaped_url}" '
                f'target="_blank" rel="noopener noreferrer">[{number}]</a></sup>'
            )
        else:
            output.append(f'<sup class="cite-ref">[{number}]</sup>')
        cursor = end
        search_from = end
    return "".join(output)


class _InlineCitationConverter(HTMLParser):
    """仅转换 HTML 文本节点中的引用，跳过 CSS、图表配置和已有链接。"""

    def __init__(self, citation_urls: dict[int, str]) -> None:
        super().__init__(convert_charrefs=True)
        self._citation_urls = citation_urls
        self._opaque_depth = 0
        self._style_depth = 0
        self.out: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.out.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.out.append(self.get_starttag_text() or f"<{tag}>")
        lowered = tag.lower()
        if lowered in _CITATION_OPAQUE_TAGS:
            self._opaque_depth += 1
        if lowered == "style":
            self._style_depth += 1

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        self.out.append(f"</{tag}>")
        lowered = tag.lower()
        if lowered in _CITATION_OPAQUE_TAGS and self._opaque_depth:
            self._opaque_depth -= 1
        if lowered == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.out.append(data)
        elif self._opaque_depth:
            self.out.append(html.escape(data, quote=False))
        else:
            self.out.append(_render_inline_citation_text(data, self._citation_urls))


def convert_inline_citations(html_text: str, pre: BriefHtmlPreprocessResult) -> str:
    """在 shell 与章节片段拼装后统一完成引用转写。"""
    if not _INLINE_CITATION_PREFIX_RE.search(html_text):
        return html_text
    citation_urls = _citation_urls(pre)
    if not citation_urls:
        return html_text
    converter = _InlineCitationConverter(citation_urls)
    converter.feed(html_text)
    converter.close()
    return "".join(converter.out)


def _strip_reference_entry_lines(text: str) -> str:
    """移除文末参考文献条目行（由 Python 从引用注册表确定性渲染）。"""
    return "\n".join(line for line in text.splitlines() if not _ENTRY_LINE_RE.match(line))


def _split_h2_blocks(text: str) -> list[str]:
    """按二级标题拆分 Markdown，忽略 fenced code block 内的 ``## `` 行。"""
    blocks: list[str] = []
    current: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
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


def _split_report_markdown(cleaned: str) -> tuple[str, str, list[BriefHtmlSectionChunk]]:
    """把清洗后的报告 markdown 拆为标题、摘要与章节块。"""
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


def _render_references_html(pre: BriefHtmlPreprocessResult, language: str) -> str:
    """从引用注册表确定性渲染参考文献区（不经过 LLM）。"""
    if not pre.reference_entries:
        return ""
    heading = "References" if language == ENGLISH else "参考文章"
    items: list[str] = []
    for number, title, url in pre.reference_entries:
        text = html.escape(title or url, quote=False)
        if url.startswith(("http://", "https://")):
            items.append(
                f'<li id="ref-{number}"><a href="{html.escape(url, quote=True)}">{text}</a></li>'
            )
        else:
            items.append(f'<li id="ref-{number}">{text}</li>')
    return f'<section class="references"><h2>{heading}</h2><ol>{"".join(items)}</ol></section>'
