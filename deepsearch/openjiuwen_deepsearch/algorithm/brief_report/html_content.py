"""Brief HTML 报告的 Markdown、引用与章节内容处理。"""

import html
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url


logger = logging.getLogger(__name__)


@dataclass
class BriefHtmlPreprocessResult:
    """预处理清洗后的 markdown 与引用元数据。

    Attributes:
        cleaned_markdown: 将行内引用规范化后的 Markdown 文本。
        reference_entries: 按编号排列的 ``(编号, 标题, URL)`` 引用条目。
    """

    cleaned_markdown: str
    reference_entries: list[tuple[int, str, str]] = field(default_factory=list)


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
    """按出现位置产出行内引用标记。

    Args:
        markdown: 待扫描的 Markdown 文本。

    Returns:
        按起始位置排序的引用匹配列表；元组依次为位置、匹配对象、固定编号和标题。
    """
    spans: list[tuple[int, re.Match, int | None, str]] = []
    for match in _CHECKED_CITATION_RE.finditer(markdown):
        spans.append((match.start(), match, int(match.group("num")), ""))
    for match in _SOURCE_TRACER_RE.finditer(markdown):
        spans.append((match.start(), match, None, match.group("title")))
    spans.sort(key=lambda item: item[0])
    return spans


def preprocess_markdown(markdown: str) -> BriefHtmlPreprocessResult:
    """把行内引用标记清洗为 ``[[n]](URL)``，并规范化文末参考文献条目。

    Args:
        markdown: 溯源校验后的 Brief Markdown 报告。

    Returns:
        清理后的 Markdown 及按编号整理的引用注册表。
    """
    entries: dict[int, tuple[str, str]] = {}
    url_to_number: dict[str, int] = {}
    # 先登记报告已有的参考文献，再给正文中新增的 URL 分配连续编号。
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
        reference_entries=[
            (number, title, url)
            for number, (title, url) in sorted(merged.items())
        ],
    )


def _citation_urls(pre: BriefHtmlPreprocessResult) -> dict[int, str]:
    """提取可确定映射的引用目标，供拼装层转写。

    Args:
        pre: Markdown 预处理结果。

    Returns:
        从引用编号到 URL 的映射。
    """
    return {
        number: url
        for number, _title, url in pre.reference_entries
        if isinstance(url, str) and url
    }


def _render_inline_citation_text(text: str, citation_urls: dict[int, str]) -> str:
    """把文本节点中的合法 Markdown 引用转成上标，丢弃失配引用。

    Args:
        text: HTML 文本节点内容。
        citation_urls: 引用编号到 URL 的确定性映射。

    Returns:
        对合法引用完成 HTML 转写并对普通文本进行转义后的字符串。
    """
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
            # 当前标记缺少合法 URL 时保留后续文本，避免整个文本节点被截断。
            search_from = match.end()
            continue
        url, end = parsed
        output.append(html.escape(text[cursor:match.start()], quote=False))
        if citation_urls.get(number) != url:
            logger.warning(
                "[BriefHtmlReporter] Dropped inline citation with mismatched URL; number=%d.",
                number,
            )
            cursor = end
            search_from = match.end()
            continue
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
    """仅转换 HTML 文本节点中的引用，跳过 CSS、图表配置和已有链接。

    Attributes:
        out: 转换后重新序列化的 HTML 片段。
    """

    def __init__(self, citation_urls: dict[int, str]) -> None:
        """初始化引用转换器。

        Args:
            citation_urls: 引用编号到 URL 的确定性映射。
        """
        super().__init__(convert_charrefs=True)
        self._citation_urls = citation_urls
        self._opaque_depth = 0
        self._style_depth = 0
        self.out: list[str] = []

    def handle_decl(self, decl: str) -> None:
        """保留 HTML 声明。

        Args:
            decl: 声明正文，例如 ``DOCTYPE html``。
        """
        self.out.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        """保留开始标签并更新不可转换区域的嵌套深度。

        Args:
            tag: 开始标签名称。
            attrs: 开始标签属性列表。
        """
        self.out.append(self.get_starttag_text() or f"<{tag}>")
        lowered = tag.lower()
        if lowered in _CITATION_OPAQUE_TAGS:
            # a、style、template 内的文本可能是 URL、CSS 或 JSON，不能按正文引用解析。
            self._opaque_depth += 1
        if lowered == "style":
            self._style_depth += 1

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        """保留自闭合标签。

        Args:
            tag: 自闭合标签名称。
            attrs: 自闭合标签属性列表。
        """
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        """保留结束标签并退出对应的不可转换区域。

        Args:
            tag: 结束标签名称。
        """
        self.out.append(f"</{tag}>")
        lowered = tag.lower()
        if lowered in _CITATION_OPAQUE_TAGS and self._opaque_depth:
            self._opaque_depth -= 1
        if lowered == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        """转换正文文本节点中的引用，并原样保留受保护区域内容。

        Args:
            data: HTMLParser 提取的文本内容。
        """
        if self._style_depth:
            self.out.append(data)
        elif self._opaque_depth:
            self.out.append(html.escape(data, quote=False))
        else:
            self.out.append(_render_inline_citation_text(data, self._citation_urls))


def convert_inline_citations(html_text: str, pre: BriefHtmlPreprocessResult) -> str:
    """在 shell 与章节片段拼装后统一完成引用转写。

    Args:
        html_text: 已拼装的 HTML 文本。
        pre: Markdown 预处理结果及引用注册表。

    Returns:
        正文引用转为上标后的 HTML 文本。
    """
    if not _INLINE_CITATION_PREFIX_RE.search(html_text):
        return html_text
    citation_urls = _citation_urls(pre)
    converter = _InlineCitationConverter(citation_urls)
    converter.feed(html_text)
    converter.close()
    return "".join(converter.out)


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


def render_references_html(pre: BriefHtmlPreprocessResult, language: str) -> str:
    """从引用注册表确定性渲染参考文献区（不经过 LLM）。

    Args:
        pre: Markdown 预处理结果及引用注册表。
        language: 报告语言标识。

    Returns:
        参考文献 ``section`` 的 HTML；没有引用时返回空字符串。
    """
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
