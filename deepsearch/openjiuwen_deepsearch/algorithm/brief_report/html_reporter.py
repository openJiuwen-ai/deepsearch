"""Brief 报告的自包含 HTML 生成：清洗、清理、校验与确定性脚本注入。"""

import asyncio
import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName


logger = logging.getLogger(__name__)


@dataclass
class BriefHtmlPreprocessResult:
    """预处理清洗后的 markdown 与引用元数据。

    Attributes:
        cleaned_markdown: 行内引用标记已清洗为 ``[[n]](URL)``（md 报告原生形态）的 markdown 正文。
        reference_entries: 规范化后的参考文献条目 ``(编号, 标题, URL)``，按编号升序。
    """

    cleaned_markdown: str
    reference_entries: list[tuple[int, str, str]] = field(default_factory=list)


_CHECKED_CITATION_RE = re.compile(r"\[checked_citation:[^\]]*\]\[\[(?P<num>\d+)\]\]\(")
_SOURCE_TRACER_RE = re.compile(
    r"(?P<image>!)?\[source_tracer_result\]\[(?P<title>.*?)\]\(", re.DOTALL
)
_ENTRY_LINE_RE = re.compile(
    r"(?ms)^\[(?P<num>\d+)\]\.\s*\[(?P<title>.*?)\]\("
)


def _reference_spans(markdown: str) -> list[tuple[int, re.Match, int | None, str]]:
    """按出现位置产出两类行内引用标记（checked 与 source_tracer，后者含 ! 图片前缀）及其元数据。

    Args:
        markdown: 待扫描的 markdown 文本。

    Returns:
        元组列表 ``(起始偏移, 匹配对象, 固定编号或 None, 标题)``，按起始偏移升序。
    """
    spans: list[tuple[int, re.Match, int | None, str]] = []
    for match in _CHECKED_CITATION_RE.finditer(markdown):
        spans.append((match.start(), match, int(match.group("num")), ""))
    for match in _SOURCE_TRACER_RE.finditer(markdown):
        spans.append((match.start(), match, None, match.group("title")))
    spans.sort(key=lambda item: item[0])
    return spans


def preprocess_markdown(markdown: str) -> BriefHtmlPreprocessResult:
    """把行内引用标记清洗为 [[n]](URL)，并规范化文末参考文献条目。

    兼容两种输入形态：溯源校验后的 ``[checked_citation:<id>][[n]](URL)`` 与
    校验跳过/异常时回退的 ``[source_tracer_result][标题](URL)``；图片引用
    ``![source_tracer_result][标题](URL)``（``!`` 前缀随匹配一起消除）按
    文本引用统一处理。同一 URL 复用编号。

    行内标记统一清洗为 md 报告原生形态 ``[[n]](URL)``（渲染后 ``[n]``
    可点击直达原网站），URL 保留在正文中供 HTML 转写使用。

    Args:
        markdown: 组装完成的 Brief 总报告 markdown（含文末参考文献条目）。

    Returns:
        清洗结果，包含清洗后 markdown、行内编号序列与规范化参考文献条目。
    """
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


_ALLOWED_TAGS = frozenset({
    "html", "head", "body", "title", "style", "meta",
    "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
    "strong", "em", "b", "i", "blockquote", "sup", "sub", "a",
    "template", "section", "footer", "header", "nav", "hr", "br",
    "main", "article", "aside", "figure", "figcaption", "details", "summary",
})
# SVG 图形需要独立的标签/属性/外链安全模型；Brief 图表只允许 ECharts 占位元素，
# 因此不加入通用 HTML 白名单，避免在此处意外放宽可执行图形载荷。
_VOID_TAGS = frozenset({"br", "hr", "meta"})
_HTML_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_ALLOWED_ATTRS = frozenset({
    "class", "id", "style", "data-chart-id", "title", "href", "target", "rel",
})
# 行内引用上标链接（md 报告原生样式）需要新窗口打开原网站；
# 取值严格受限以防滥用。
_TARGET_ALLOWED = frozenset({"_blank"})
_REL_ALLOWED = frozenset({"noopener", "noreferrer"})
_CHART_CONFIGS_TEMPLATE_ID = "chart-configs"
_CHART_CONFIGS_OPEN_TAG_RE = re.compile(
    r'''(?is)<template\b(?=[^>]*\bid\s*=\s*(?:"chart-configs"|'chart-configs'|chart-configs\b))[^>]*>'''
)
_CHART_CONFIGS_CLOSE_TAG_RE = re.compile(r"(?is)</template\s*>")
_CHART_CONFIGS_FORBIDDEN_TAG_BLOCK_RE = re.compile(
    r"(?is)<(?:script|iframe|object|embed|style)\b[^>]*>.*?</(?:script|iframe|object|embed|style)\s*>"
)
_CHART_CONFIGS_INNER_CLOSE_TAG_RE = re.compile(r"(?is)</template\s*>")


def _json_brackets_balanced(text: str) -> bool:
    """判断 JSON 集合括号（含字符串感知）是否平衡闭合。

    Args:
        text: 待检查的 JSON 文本片段。

    Returns:
        方括号与花括号均平衡且不在未闭合字符串中时返回 True。
    """
    square = curly = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
        if square < 0 or curly < 0:
            return False
    return square == 0 and curly == 0 and not in_string


def _escape_chart_config_template_payloads(html_text: str) -> str:
    """把 chart-configs 的原始 JSON 载荷转义为 HTML 文本节点。

    HTMLParser 会将 JSON 字符串中的 ``<Android`` 等内容误识别为标签。
    此处仅对完整 JSON 后的真正 ``</template>`` 作为闭合标志，把载荷转义后
    再交给 sanitizer；伪造闭合标签与危险容器会从载荷中移除。

    Args:
        html_text: 包含 LLM 原始 HTML 的字符串。

    Returns:
        chart-configs 载荷已 HTML 转义的 HTML 字符串。
    """
    parts: list[str] = []
    cursor = 0
    while match := _CHART_CONFIGS_OPEN_TAG_RE.search(html_text, cursor):
        parts.append(html_text[cursor:match.end()])
        payload_start = match.end()
        closing = None
        for candidate in _CHART_CONFIGS_CLOSE_TAG_RE.finditer(html_text, payload_start):
            raw_payload = html_text[payload_start:candidate.start()]
            if _json_brackets_balanced(html.unescape(raw_payload)):
                closing = candidate
                break
        if closing is None:
            # 不完整的 template 仍交给 sanitizer 收敛为空模板；先转义以免其中的
            # 字符串内容被 HTMLParser 当作实际标签解析。
            parts.append(html.escape(html.unescape(html_text[payload_start:]), quote=False))
            cursor = len(html_text)
            break
        payload = html.unescape(html_text[payload_start:closing.start()])
        payload = _CHART_CONFIGS_FORBIDDEN_TAG_BLOCK_RE.sub("", payload)
        payload = _CHART_CONFIGS_INNER_CLOSE_TAG_RE.sub("", payload)
        parts.append(html.escape(payload, quote=False))
        parts.append(closing.group(0))
        cursor = closing.end()
    parts.append(html_text[cursor:])
    return "".join(parts)


class _Sanitizer(HTMLParser):
    """白名单清理器：非白名单标签连同内容删除，输出重新序列化的文档。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0
        self._skip_tag = ""
        self._head_depth = 0
        self._style_depth = 0
        self._tag_stack: list[str] = []
        self._chart_mode = False
        self._chart_drop_depth = 0
        self._chart_parts: list[str] = []

    def handle_decl(self, decl: str) -> None:
        if not self._skip_depth and decl.lower().startswith("doctype"):
            self.out.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if self._chart_mode:
            # chart-configs 内容只允许纯文本：void 标签丢弃，其余连同内容删除。
            if tag not in _HTML_VOID_TAGS:
                self._chart_drop_depth += 1
            return
        if tag not in _ALLOWED_TAGS:
            if tag in _HTML_VOID_TAGS:
                # 非白名单 void 标签（img/embed 等）无内容，直接丢弃标签本身。
                return
            self._skip_depth, self._skip_tag = 1, tag
            return
        if tag == "style" and self._head_depth == 0:
            self._skip_depth, self._skip_tag = 1, tag
            return
        if tag == "template":
            if not self._is_chart_configs_template(attrs):
                self._skip_depth, self._skip_tag = 1, tag
                return
            rendered = self._render_attrs(tag, attrs)
            self._tag_stack.append(tag)
            self.out.append(f"<{tag}{rendered}>")
            self._chart_mode = True
            self._chart_drop_depth = 0
            self._chart_parts = []
            return
        if tag == "meta":
            attrs = self._filter_meta_attrs(attrs)
        rendered = self._render_attrs(tag, attrs)
        if tag in _VOID_TAGS:
            self.out.append(f"<{tag}{rendered}>")
            return
        self._tag_stack.append(tag)
        if tag == "head":
            self._head_depth += 1
        if tag == "style":
            self._style_depth += 1
        self.out.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = ""
            return
        if self._chart_mode:
            if tag == "template":
                content = "".join(self._chart_parts)
                # 仅当 JSON 括号平衡且不在删除模式时才视为真正的闭合标签；
                # 注入的伪造 </template> 连同文本一起消失。
                if self._chart_drop_depth == 0 and _json_brackets_balanced(content):
                    self.out.append(html.escape(content, quote=False))
                    self._tag_stack.pop()
                    self.out.append("</template>")
                    self._chart_mode = False
                    self._chart_parts = []
            elif self._chart_drop_depth > 0:
                self._chart_drop_depth -= 1
            return
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag not in self._tag_stack:
            return
        while self._tag_stack:
            open_tag = self._tag_stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == "head":
                self._head_depth -= 1
            if open_tag == "style":
                self._style_depth -= 1
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._chart_mode:
            if self._chart_drop_depth == 0:
                self._chart_parts.append(data)
            return
        if self._style_depth:
            self.out.append(data)
        else:
            self.out.append(html.escape(data, quote=False))

    def finalize(self) -> None:
        """feed 结束后关闭未闭合标签，保证输出文档结构完整。"""
        if self._chart_mode:
            # 畸形 chart-configs template：丢弃未完成内容，仅保留空 template。
            self._chart_mode = False
            self._chart_parts = []
            self._chart_drop_depth = 0
        while self._tag_stack:
            open_tag = self._tag_stack.pop()
            if open_tag == "head":
                self._head_depth -= 1
            if open_tag == "style":
                self._style_depth -= 1
            self.out.append(f"</{open_tag}>")

    @staticmethod
    def _is_chart_configs_template(attrs: list) -> bool:
        return any(name == "id" and value == _CHART_CONFIGS_TEMPLATE_ID for name, value in attrs)

    @staticmethod
    def _filter_meta_attrs(attrs: list) -> list:
        has_viewport = any(
            name and name.lower() == "name" and (value or "").strip().lower() == "viewport"
            for name, value in attrs
        )
        kept: list[tuple[str, str | None]] = []
        for name, value in attrs:
            if name is None:
                continue
            lowered = name.lower()
            if lowered == "charset":
                kept.append((name, value))
            elif has_viewport and lowered in {"name", "content"}:
                kept.append((name, value))
        return kept

    def _render_attrs(self, tag: str, attrs: list) -> str:
        allowed = _ALLOWED_ATTRS if tag != "meta" else {"charset", "name", "content"}
        parts: list[str] = []
        for name, value in attrs:
            if not name:
                continue
            lowered = name.lower()
            if lowered.startswith("on") or lowered not in allowed:
                continue
            text = value or ""
            if lowered == "href":
                stripped = text.strip().lower()
                # 页内锚点（# 开头）无外联/脚本风险，与外链一并放行：
                # 外链供行内引用上标直达原网站（md 报告原生行为），
                # 锚点供 TOC 等页内导航。
                if not stripped.startswith(("http://", "https://", "#")):
                    continue
            elif lowered == "target":
                if text.strip() not in _TARGET_ALLOWED:
                    continue
            elif lowered == "rel":
                tokens = set(text.split())
                if not tokens or not tokens <= _REL_ALLOWED:
                    continue
            parts.append(f' {lowered}="{html.escape(text, quote=True)}"')
        return "".join(parts)


def sanitize_html(html_text: str) -> str:
    """按白名单清理 LLM 输出的 HTML 并重新序列化完整文档。

    Args:
        html_text: LLM 生成的原始 HTML 文档。

    Returns:
        仅保留白名单标签/属性、重新序列化后的完整 HTML 文档字符串。
    """
    parser = _Sanitizer()
    parser.feed(_escape_chart_config_template_payloads(html_text))
    parser.close()
    parser.finalize()
    return "".join(parser.out)


_SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"javascript:", re.IGNORECASE)
_CHART_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_ESCAPE_RE = re.compile(r"\\([0-9A-Fa-f]{1,6})\s?")
_FORBIDDEN_URL_PAYLOADS = ("http://", "https://", "image://", "data:", "javascript:")
_INLINE_CITATION_PREFIX_RE = re.compile(r"\[\[(?P<number>\d+)\]\]\(")
_CITATION_OPAQUE_TAGS = frozenset({"a", "style", "template"})
_GAP_SENSITIVE_SERIES_TYPES = frozenset({"line", "area"})
_RATIO_SERIES_KEYWORDS = (
    "占比",
    "占gdp",
    "比例",
    "份额",
    "share",
    "ratio",
    "rate",
    "percent",
    "percentage",
    "%",
)


class _EventAttributeScanner(HTMLParser):
    """检测实际 HTML 元素属性中的事件处理器。

    Attributes:
        has_event_attribute: 是否遇到任意 ``on*`` 形式的元素属性。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_event_attribute = False

    def handle_starttag(self, _tag: str, attrs: list) -> None:
        """记录当前开始标签中出现的事件属性。

        Args:
            _tag: HTML 标签名。
            attrs: HTMLParser 解析出的属性键值对。
        """
        self.has_event_attribute |= any(
            name and name.lower().startswith("on") for name, _value in attrs
        )

    handle_startendtag = handle_starttag


def _contains_event_attribute(html_text: str) -> bool:
    """判断 HTML 中是否存在实际元素事件属性。

    Args:
        html_text: 待检查的 HTML 文档或片段。

    Returns:
        存在任意 ``on*`` 元素属性时返回 True。
    """
    scanner = _EventAttributeScanner()
    scanner.feed(html_text)
    scanner.close()
    return scanner.has_event_attribute


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


def convert_inline_citations(
    html_text: str, pre: BriefHtmlPreprocessResult
) -> str:
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


def validate_chart_option(option: object) -> str | None:
    """递归校验 ECharts option。

    Args:
        option: 解析后的图表配置对象。

    Returns:
        校验失败时返回错误描述；通过时返回 None。
    """
    if not isinstance(option, dict):
        return "chart option must be a JSON object"
    return _validate_option_node(option, "option")


def _validate_option_node(node: object, path: str) -> str | None:
    """递归校验 option 节点：类型受限、字符串拒 URL 载荷、formatter 拒 HTML。"""
    if node is None or isinstance(node, (bool, int, float)):
        return None
    if isinstance(node, str):
        lowered = node.lower()
        for payload in _FORBIDDEN_URL_PAYLOADS:
            if payload in lowered:
                return f"{path} contains forbidden URL payload: {payload}"
        return None
    if isinstance(node, list):
        for index, item in enumerate(node):
            error = _validate_option_node(item, f"{path}[{index}]")
            if error:
                return error
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "formatter" and isinstance(value, str) and ("<" in value or ">" in value):
                return f"{path}.{key} formatter must not contain HTML tags"
            error = _validate_option_node(value, f"{path}.{key}")
            if error:
                return error
        return None
    return f"{path} has unsupported type {type(node).__name__}"


def _chart_axis_at(option: dict, axis_name: str, index: int = 0) -> dict | None:
    """读取 ECharts 单轴或多轴配置中的指定轴。

    Args:
        option: ECharts option 配置。
        axis_name: 轴字段名，例如 ``xAxis`` 或 ``yAxis``。
        index: 多轴配置中的轴索引。

    Returns:
        指定轴配置字典；字段不存在或类型不符合预期时返回 None。
    """
    axes = option.get(axis_name)
    if isinstance(axes, dict):
        return axes if index == 0 else None
    if isinstance(axes, list) and 0 <= index < len(axes) and isinstance(axes[index], dict):
        return axes[index]
    return None


def _chart_category_values(option: dict) -> list[object] | None:
    """读取分类横轴的完整类别列表。

    Args:
        option: ECharts option 配置。

    Returns:
        ``xAxis.data`` 的类别列表；没有显式分类轴数据时返回 None。
    """
    axis = _chart_axis_at(option, "xAxis")
    if not isinstance(axis, dict) or not isinstance(axis.get("data"), list):
        return None
    return axis["data"]


def _chart_data_value(item: object) -> object:
    """提取 ECharts 数据项的实际值，用于识别 null 缺口。

    Args:
        item: ECharts primitive、带 ``value`` 的对象或维度数组。

    Returns:
        数据项的值；空对象、空数组或显式空值返回 None。
    """
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get("value")
    if isinstance(item, list):
        return item[-1] if item else None
    return item


def _align_named_chart_data(data: list[object], categories: list[object]) -> list[object] | None:
    """按数据项 name 将稀疏的命名序列对齐到分类轴。

    Args:
        data: ECharts series.data 列表。
        categories: xAxis.data 分类列表。

    Returns:
        成功对齐后的等长列表；数据项不是唯一命名对象或包含未知类别时返回 None。
    """
    if not data or not all(
        isinstance(item, dict) and "name" in item and "value" in item for item in data
    ):
        return None
    category_keys = [str(category) for category in categories]
    data_by_name = {str(item["name"]): item for item in data}
    data_keys = list(data_by_name)
    if len(data_keys) != len(data) or any(key not in category_keys for key in data_keys):
        return None
    return [data_by_name.get(key) for key in category_keys]


def _chart_series_axis_name(option: dict, series: dict) -> str:
    """读取序列绑定的 Y 轴名称，辅助识别比例类指标。

    Args:
        option: ECharts option 配置。
        series: 单个 series 配置。

    Returns:
        序列名称与绑定 Y 轴名称拼接后的文本。
    """
    axis_index = series.get("yAxisIndex", 0)
    if not isinstance(axis_index, int) or isinstance(axis_index, bool):
        axis_index = 0
    axis = _chart_axis_at(option, "yAxis", axis_index)
    axis_name = axis.get("name", "") if isinstance(axis, dict) else ""
    return f"{series.get('name', '')} {axis_name}".casefold()


def _is_ratio_chart_series(option: dict, series: dict) -> bool:
    """判断序列是否表示占比、比例或百分比指标。

    Args:
        option: ECharts option 配置。
        series: 单个 series 配置。

    Returns:
        名称或绑定 Y 轴名称包含比例类关键词时返回 True。
    """
    text = _chart_series_axis_name(option, series)
    return any(keyword.casefold() in text for keyword in _RATIO_SERIES_KEYWORDS)


def _legend_item_name(item: object) -> str | None:
    """提取 legend.data 中字符串或对象形式的系列名称。

    Args:
        item: legend.data 的单个项目。

    Returns:
        图例名称；无法识别时返回 None。
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("name"), str):
        return item["name"]
    return None


def _prune_chart_axes_and_legend(option: dict, series: list[dict]) -> None:
    """删除已无序列使用的 Y 轴并同步收缩图例。

    Args:
        option: 待更新的 ECharts option 配置。
        series: 归一化后保留的 series 列表。

    Returns:
        None. 函数原地更新 option。
    """
    y_axes = option.get("yAxis")
    if isinstance(y_axes, list):
        if not series:
            option["yAxis"] = []
        else:
            used_indexes: set[int] = set()
            for item in series:
                axis_index = item.get("yAxisIndex", 0)
                if not isinstance(axis_index, int) or isinstance(axis_index, bool):
                    axis_index = 0
                if 0 <= axis_index < len(y_axes):
                    used_indexes.add(axis_index)
            if used_indexes:
                kept_indexes = sorted(used_indexes)
                index_map = {old: new for new, old in enumerate(kept_indexes)}
                option["yAxis"] = [y_axes[index] for index in kept_indexes]
                for item in series:
                    axis_index = item.get("yAxisIndex", 0)
                    if not isinstance(axis_index, int) or isinstance(axis_index, bool):
                        axis_index = 0
                    if "yAxisIndex" in item:
                        item["yAxisIndex"] = index_map.get(axis_index, 0)

    legend = option.get("legend")
    if isinstance(legend, dict) and isinstance(legend.get("data"), list):
        series_names = {
            item.get("name")
            for item in series
            if isinstance(item.get("name"), str)
        }
        if series_names:
            legend["data"] = [
                item
                for item in legend["data"]
                if _legend_item_name(item) in series_names
            ]


def normalize_chart_option(option: dict) -> tuple[dict, list[str]]:
    """按分类轴归一化图表数据，并降级不完整的比例类折线。

    分类轴存在时，序列数据必须与类别数量一致；可通过 ``name`` 对齐的稀疏
    数据会先补齐为显式空值，无法安全对齐的序列直接丢弃。普通折线保留空值
    但强制 ``connectNulls`` 为 False；比例、占比和百分比折线只要存在缺失
    类别就整条移除，避免把不连续观测误读成连续趋势。

    Args:
        option: 已通过基础安全校验的 ECharts option，函数会原地更新它。

    Returns:
        ``(归一化后的 option, warning 文本列表)``。
    """
    categories = _chart_category_values(option)
    raw_series = option.get("series")
    if isinstance(raw_series, dict):
        series = [raw_series]
        series_is_object = True
    elif isinstance(raw_series, list):
        series = [item for item in raw_series if isinstance(item, dict)]
        series_is_object = False
    else:
        return option, []

    warnings: list[str] = []
    kept_series: list[dict] = []
    category_count = len(categories) if categories is not None else None
    for item in series:
        series_name = str(item.get("name") or "<unnamed>")
        series_type = str(item.get("type") or "").casefold()
        data = item.get("data")
        if not isinstance(data, list):
            kept_series.append(item)
            continue

        if categories is not None:
            has_named_data = bool(data) and all(
                isinstance(data_item, dict)
                and "name" in data_item
                and "value" in data_item
                for data_item in data
            )
            aligned = _align_named_chart_data(data, categories)
            if aligned is not None:
                data = aligned
                item["data"] = data
            elif has_named_data:
                warnings.append(
                    f"chart_series_category_mismatch: {series_name}"
                )
                continue
            elif len(data) != category_count:
                warnings.append(
                    f"chart_series_length_mismatch: {series_name} "
                    f"(expected {category_count}, got {len(data)})"
                )
                continue

        missing_indexes = [
            index for index, data_item in enumerate(data)
            if _chart_data_value(data_item) is None
        ]
        if series_type in _GAP_SENSITIVE_SERIES_TYPES:
            # 即使没有缺口也显式关闭，防止后续 option 合并或 ECharts 默认行为跨空点连接。
            item["connectNulls"] = False
        if missing_indexes and _is_ratio_chart_series(option, item):
            missing_labels = (
                [str(categories[index]) for index in missing_indexes if categories is not None]
                or [str(index) for index in missing_indexes]
            )
            warnings.append(
                f"chart_series_dropped_incomplete: {series_name} "
                f"(missing categories: {', '.join(missing_labels)})"
            )
            continue
        kept_series.append(item)

    if series_is_object:
        option["series"] = kept_series[0] if len(kept_series) == 1 else kept_series
    else:
        option["series"] = kept_series
    _prune_chart_axes_and_legend(option, kept_series)
    return option, warnings


class _HtmlStructureScanner(HTMLParser):
    """收集图表占位、配置与 CSS。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chart_ids: list[str] = []
        self.chart_configs_raw: str | None = None
        self.style_blocks: list[str] = []
        self.inline_styles: list[str] = []
        self._in_style = 0
        self._in_template_configs = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_map = {name: value for name, value in attrs if name}
        if attr_map.get("data-chart-id"):
            self.chart_ids.append(attr_map["data-chart-id"])
        elif tag == "template" and attr_map.get("id") == _CHART_CONFIGS_TEMPLATE_ID:
            self._in_template_configs += 1
        elif tag == "style":
            self._in_style += 1
        if attr_map.get("style"):
            self.inline_styles.append(attr_map["style"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "template" and self._in_template_configs:
            self._in_template_configs -= 1
        elif tag == "style" and self._in_style:
            self._in_style -= 1

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style_blocks.append(data)
            return
        if self._in_template_configs:
            self.chart_configs_raw = (self.chart_configs_raw or "") + data
            return


def _decode_css_escape(match: re.Match) -> str:
    """把 CSS 十六进制转义（如 ``\\75 `` → ``u``）解码为对应字符。"""
    try:
        codepoint = int(match.group(1), 16)
    except ValueError:
        return ""
    if 0 < codepoint <= 0x10FFFF:
        return chr(codepoint)
    return ""


def _normalize_css(css: str) -> str:
    """去注释、解码转义序列与空白后小写化，供外部引用检测。"""
    css = _CSS_COMMENT_RE.sub("", css)
    css = _CSS_ESCAPE_RE.sub(_decode_css_escape, css)
    return re.sub(r"\s+", "", css).lower()


def _css_has_external_reference(css: str) -> bool:
    """检测 CSS 中的 url() 与 @import 外部引用（规范化后大小写不敏感）。"""
    normalized = _normalize_css(css)
    return "url(" in normalized or "@import" in normalized


def _validate_css_references(scanner: _HtmlStructureScanner) -> list[str]:
    """校验 <style> 块与内联 style 属性均不含外部引用。"""
    for css in [*scanner.style_blocks, *scanner.inline_styles]:
        if css and _css_has_external_reference(css):
            return ["css_external_reference"]
    return []


def _validate_chart_configs(scanner: _HtmlStructureScanner) -> tuple[list[str], list[str]]:
    """校验图表配置 JSON、id 格式与占位元素一一对应。

    占位元素存在但 ``template#chart-configs`` 缺失属于可确定性修复的
    保真缺陷（注入阶段直接移除占位元素，布局无损收缩），降级为警告；
    template 存在但内容非法（JSON 坏 / id 不匹配 / option 含 URL）仍为
    硬错误——说明 LLM 确实想画 ECharts 图而配置本身不合规，必须重试。

    Args:
        scanner: 清理后 HTML 的结构扫描结果。

    Returns:
        ``(errors, warnings)`` 二元组。
    """
    if scanner.chart_configs_raw is None and not scanner.chart_ids:
        return [], []
    if scanner.chart_configs_raw is None:
        return [], [
            "chart_config: placeholders present but template#chart-configs missing "
            f"(placeholders={sorted(scanner.chart_ids)}); they will be stripped at injection"
        ]
    try:
        configs = json.loads(scanner.chart_configs_raw)
    except ValueError as exc:
        return [f"chart_config: invalid JSON ({exc})"], []
    if not isinstance(configs, list) or not all(isinstance(item, dict) for item in configs):
        return ["chart_config: configs must be a JSON array of objects"], []
    config_ids: list[str] = []
    for index, config in enumerate(configs):
        config_id = config.get("id")
        if not isinstance(config_id, str) or not _CHART_ID_RE.match(config_id):
            return [f"chart_config: invalid chart id at index {index}"], []
        config_ids.append(config_id)
        error = validate_chart_option(config.get("option"))
        if error:
            return [f"chart_config: {error} (chart {config_id})"], []
    for chart_id in scanner.chart_ids:
        if not isinstance(chart_id, str) or not _CHART_ID_RE.match(chart_id):
            return [f"chart_config: invalid data-chart-id {chart_id!r}"], []
    if sorted(config_ids) != sorted(scanner.chart_ids):
        return [
            "chart_config: chart ids between placeholders and configs must match one-to-one "
            f"(placeholders={sorted(scanner.chart_ids)}, configs={sorted(config_ids)})"
        ], []
    return [], []


def validate_html_report(html: str) -> tuple[list[str], list[str]]:
    """对清理后的 HTML 执行安全与图表结构校验。

    校验分两级（对齐 report_full_html MVP 的成功率策略）：

    - **errors（硬校验）**：结构闭合、script/事件属性/危险 URL、CSS 外链、
      图表占位与配置一致性。违反安全契约必须重试。
    - **warnings**：图表占位存在但配置模板缺失，注入阶段会移除占位元素。

    Args:
        html: 清理后的完整 HTML 文档。

    Returns:
        ``(errors, warnings)`` 二元组；errors 为空表示安全校验通过。
    """
    errors: list[str] = []
    warnings: list[str] = []
    lowered = html.lower()
    if "<!doctype html" not in lowered:
        errors.append("missing_doctype")
    for tag in ("html", "head", "body"):
        if f"<{tag}" not in lowered or f"</{tag}>" not in lowered:
            errors.append(f"missing_or_unclosed_{tag}")
    if _SCRIPT_TAG_RE.search(html):
        errors.append("script_tag_present")
    if _JAVASCRIPT_URL_RE.search(html):
        errors.append("javascript_url_present")

    scanner = _HtmlStructureScanner()
    scanner.feed(html)
    scanner.close()
    if _contains_event_attribute(html):
        errors.append("event_attribute_present")
    errors.extend(_validate_css_references(scanner))
    chart_errors, chart_warnings = _validate_chart_configs(scanner)
    errors.extend(chart_errors)
    warnings.extend(chart_warnings)
    return errors, warnings


def _insert_before(html_text: str, closing_tag: str, payload: str) -> str:
    """把 payload 插入到 closing_tag 最后一次出现处之前。

    必须用 rfind 定位真正的结构闭合标签：head 中的 CSS 与已注入的
    echarts 库脚本内部都可能包含与闭合标签同名的字符串字面量，
    最先出现的匹配可能是假锚点（会把注入内容埋进脚本或 CSS 文本中），
    而文档真正的闭合标签总是最后一次出现。找不到时原样返回。

    Args:
        html_text: 待注入的 HTML 文档。
        closing_tag: 结构闭合标签（如 ``</body>``、``</head>``）。
        payload: 插入到闭合标签之前的完整内容。

    Returns:
        完成注入的 HTML 文档；未找到闭合标签时返回原文。
    """
    index = html_text.rfind(closing_tag)
    if index < 0:
        return html_text
    return html_text[:index] + payload + html_text[index:]


_ECHARTS_ASSET_PATH = Path(__file__).parent / "assets" / "echarts.min.js"
ECHARTS_SHA256 = "bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1"
_ECHARTS_LIB_MARKER = "<!--openjiuwen:echarts-lib-->"
_ECHARTS_LIB_END = "<!--/openjiuwen:echarts-lib-->"
_CHART_SCRIPT_MARKER = "<!--openjiuwen:chart-init-->"
_CHART_SCRIPT_END = "<!--/openjiuwen:chart-init-->"
_TEMPLATE_BLOCK_RE = re.compile(
    r'''(?is)<template\b(?=[^>]*\bid\s*=\s*(?:"chart-configs"|'chart-configs'|chart-configs\b))[^>]*>(.*?)</template\s*>'''
)
# 图表占位元素（prompt 约定为空内容 div，class 必含 echarts-chart）；
# template 缺失时的降级移除依赖它。
_CHART_PLACEHOLDER_RE = re.compile(
    r'(?is)<div\b[^>]*class="[^"]*echarts-chart[^"]*"[^>]*>.*?</div>'
)
_MARKED_BLOCK_RE = re.compile(
    r"(?s)<!--openjiuwen:(?:echarts-lib|chart-init)-->.*?<!--/openjiuwen:(?:echarts-lib|chart-init)-->"
)


def _has_renderable_chart(html_text: str) -> bool:
    """判断 HTML 中是否存在需要 ECharts 运行时的有效图表配置。

    Args:
        html_text: 待检查的 HTML 文档，可能仍包含配置 template 或已生成的初始化脚本。

    Returns:
        存在至少一项图表配置或初始化脚本时返回 True；否则返回 False。
    """
    if _CHART_SCRIPT_MARKER in html_text:
        return True
    match = _TEMPLATE_BLOCK_RE.search(html_text)
    if match is None:
        return False
    try:
        configs = json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError):
        return False
    return isinstance(configs, list) and bool(configs)


def _load_echarts_source() -> str:
    """读取并校验内嵌 ECharts vendor 文件；缺失或哈希不符视为环境错误。"""
    if not _ECHARTS_ASSET_PATH.is_file():
        raise FileNotFoundError("echarts vendor asset is missing")
    data = _ECHARTS_ASSET_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != ECHARTS_SHA256:
        raise ValueError("echarts vendor asset sha256 mismatch")
    return data.decode("utf-8")


def inject_chart_scripts(html_text: str) -> str:
    """从 template 提取图表配置并以固定模板生成初始化脚本。

    输入为 sanitizer 重新序列化后的 HTML，template 内容中的 ``<``/``>``/``&``
    已被转义为 HTML 实体，提取后先 ``html.unescape`` 还原再 ``json.loads``
    （与校验阶段 ``_HtmlStructureScanner`` 的 convert_charrefs 解码一致），
    随后重新以 ``ensure_ascii=True`` 序列化，并将 ``<``/``>`` 替换为
    ``\\u003c``/``\\u003e``，杜绝 ``</script>`` 提前闭合与实体注入；
    每个 option 确定性强制合并 richText tooltip。

    Args:
        html_text: 清理与校验后的 HTML 文档（含 chart-configs template）。

    Returns:
        template 被替换为初始化脚本（插入 ``</body>`` 前）的 HTML 文档；
        template 缺失但存在图表占位元素时，移除占位元素后原样返回
        （对应校验阶段的降级警告：无配置的占位只是空白块，移除后布局无损收缩）。
    """
    match = _TEMPLATE_BLOCK_RE.search(html_text)
    if match is None:
        stripped = _CHART_PLACEHOLDER_RE.sub("", html_text)
        if stripped != html_text:
            logger.warning(
                "[BriefHtmlReporter] Stripped %d chart placeholder(s) without template#chart-configs.",
                len(_CHART_PLACEHOLDER_RE.findall(html_text)),
            )
        return stripped
    configs = json.loads(html.unescape(match.group(1)))
    if not configs:
        # 空配置不产生初始化脚本，避免后续误判为需要加载 ECharts runtime。
        without_template = _TEMPLATE_BLOCK_RE.sub("", html_text)
        return _CHART_PLACEHOLDER_RE.sub("", without_template)
    for config in configs:
        option = config.get("option") or {}
        tooltip = option.get("tooltip")
        if isinstance(tooltip, dict):
            tooltip["renderMode"] = "richText"
        else:
            option["tooltip"] = {"renderMode": "richText"}
    payload = json.dumps(configs, ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    script = (
        f"{_CHART_SCRIPT_MARKER}<script>\n"
        "(function () {\n"
        '  "use strict";\n'
        f"  var configs = {payload};\n"
        "  var render = function () {\n"
        "    for (var i = 0; i < configs.length; i++) {\n"
        "      var el = document.querySelector('[data-chart-id=\"' + configs[i].id + '\"]');\n"
        "      if (!el) { continue; }\n"
        "      window.echarts.init(el).setOption(configs[i].option);\n"
        "    }\n"
        "  };\n"
        '  if (document.readyState === "loading") {\n'
        '    document.addEventListener("DOMContentLoaded", render);\n'
        "  } else { render(); }\n"
        "})();\n"
        f"</script>{_CHART_SCRIPT_END}"
    )
    without_template = _TEMPLATE_BLOCK_RE.sub("", html_text)
    return _insert_before(without_template, "</body>", f"{script}\n")


def inject_echarts_library(html: str) -> str:
    """按需把通过 SHA-256 校验的 echarts.min.js 以内联脚本注入 head。

    Args:
        html: 清理后的 HTML 文档。

    Returns:
        有图表时返回 head（或 body 顶部兜底）内联 ECharts 库脚本的 HTML 文档；
        无图表时原样返回，避免加载约 1 MB 的运行时。
    """
    if not _has_renderable_chart(html):
        return html
    source = _load_echarts_source()
    block = f"{_ECHARTS_LIB_MARKER}<script>{source}</script>{_ECHARTS_LIB_END}"
    if "</head>" in html:
        return _insert_before(html, "</head>", f"{block}\n")
    return _insert_before(html, "</body>", f"{block}\n")


def inject_ai_notice(html: str, language: str) -> str:
    """在 body 末尾确定性插入 AI 生成声明（随语言切换）。

    样式全部内联并使用 CSS 变量 fallback：文档按 prompt 约定定义了
    ``--border``/``--text-muted``/``--accent`` 等变量时自动融合报告
    主题色，未定义时退回中性灰，不依赖文档内的任何 class。

    Args:
        html: 清理后的 HTML 文档。
        language: 报告语言（zh-CN / en）。

    Returns:
        body 末尾带 AI 生成声明 footer 的 HTML 文档。
    """
    text = (
        "This research report was generated by AI and is for reference only."
        if language == ENGLISH
        else "本研究报告由 AI 生成，仅供参考"
    )
    notice = (
        '<footer style="margin-top:48px;padding:26px 16px 10px;'
        'border-top:1px solid var(--border,#e8e6e0);text-align:center;">'
        '<p style="margin:0;font-size:12px;line-height:1.7;'
        'color:var(--text-muted,#8f8f8b);letter-spacing:.3px;">'
        '<span style="display:inline-block;padding:2px 10px;margin-right:8px;'
        'border-radius:999px;background:var(--accent-light,#f0efec);'
        'color:var(--accent,#6b6b68);font-weight:600;font-size:11px;'
        'line-height:1.5;">AI</span>'
        f"{text}</p></footer>"
    )
    return _insert_before(html, "</body>", f"{notice}\n")


def final_security_assert(html: str) -> None:
    """终检断言：除系统注入的脚本外，产物无任何脚本/事件/外部引用。

    Args:
        html: 完整注入链路后的 HTML 产物。

    Raises:
        RuntimeError: 存在系统脚本之外的任何脚本/事件属性/外部引用时抛出。
    """
    residual = _MARKED_BLOCK_RE.sub("", html)
    problems: list[str] = []
    if _SCRIPT_TAG_RE.search(residual):
        problems.append("unexpected script tag")
    if _contains_event_attribute(residual):
        problems.append("event attribute")
    if _JAVASCRIPT_URL_RE.search(residual):
        problems.append("javascript url")
    for tag in ("img", "link", "iframe", "object", "embed"):
        if re.search(rf"<{tag}\b", residual, re.IGNORECASE):
            problems.append(f"{tag} tag")
    if problems:
        raise RuntimeError(
            f"brief html final security check failed: {'; '.join(problems)}"
        )


_HTML_REPORT_BLOCK_RE = re.compile(r"(?s)<html_report>\s*(.*?)\s*</html_report>")


def _extract_html_body(raw: str) -> str | None:
    """从 LLM 原始输出中提取 HTML 文档主体。

    优先匹配 ``<html_report>...</html_report>`` 包裹标签；未匹配时兜底
    直接提取 ``<!doctype`` 起始、``</html>`` 结束的裸文档（对齐
    report_full_html MVP 的提取方式，模型忘写包裹标签或用 markdown
    围栏包裹时同样可恢复）。两种方式都失败（典型为输出截断）返回 None。

    Args:
        raw: LLM 原始输出文本。

    Returns:
        提取出的 HTML 文档主体；无法提取时返回 None。
    """
    match = _HTML_REPORT_BLOCK_RE.search(raw)
    if match is not None:
        return match.group(1)
    lowered = raw.lower()
    start = lowered.find("<!doctype")
    if start < 0:
        return None
    end = lowered.rfind("</html>")
    if end >= 0:
        return raw[start:end + len("</html>")].strip()
    # 文档完整但缺最外层 </html> 闭合（输出以 </html_report> 结尾）：
    # 补上闭合标签即可恢复，不应误判为截断而触发整轮重试。
    if "</body>" in lowered:
        return raw[start:].rstrip().removesuffix("</html_report>").rstrip() + "\n</html>"
    return None


@dataclass
class BriefHtmlSectionChunk:
    """报告 markdown 按 ``## `` 拆分出的单个章节块。"""

    section_id: str
    title: str
    markdown: str


_H1_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_H2_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_FENCED_CODE_DELIMITER_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_SUMMARY_HEADINGS = frozenset({"核心摘要", "Executive Summary"})
_REFERENCES_HEADINGS = frozenset({"参考文章", "References"})


def _strip_reference_entry_lines(text: str) -> str:
    """移除文末参考文献条目行（由 Python 从引用注册表确定性渲染）。"""
    return "\n".join(
        line for line in text.splitlines() if not _ENTRY_LINE_RE.match(line)
    )


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
    """把清洗后的报告 markdown 拆为标题、摘要与章节块。

    结构契约（writer.assemble_brief_report）：``# 标题`` + ``## 核心摘要`` +
    逐章 ``## {id} {title}`` + ``## 参考文章``。参考文献节不进入 LLM——条目
    从引用注册表确定性渲染；正文中的参考文献条目行也一并剔除。

    Args:
        cleaned: 预处理清洗后的报告 markdown。

    Returns:
        ``(标题, 摘要md, 章节块列表)``；无对应结构时摘要为空串、章节为空列表。
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
            # 无 ## 标题的前导部分（# 标题）或噪声，不作为章节。
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


def _error_feedback_lines(errors: list[str]) -> list[str]:
    """把上一轮错误格式化为可执行的 LLM 反馈；空错误返回空列表。"""
    if not errors:
        return []
    lines: list[str] = []
    for error in errors:
        lines.append(f"- {error}")
        if "chart_config" in error:
            # 机器错误码对 LLM 可执行性差，附上双路径修复指引：
            # 占位与配置必须成对出现，二选一修复。章节级错误带
            # ``section <id>:`` 前缀，须用包含判断而非前缀判断。
            lines.append(
                "  Fix: ECharts placeholders (<div class=\"echarts-chart\" data-chart-id=\"...\">) and "
                "the config block (<template id=\"chart-configs\">[...]</template> at the end) MUST appear "
                "in pairs with matching ids. Either remove ALL placeholder divs and render those charts as "
                "CSS bar rows instead, or add/fix the template block so every placeholder has one config "
                "entry with the same id."
            )
    lines.insert(
        0,
        "Your previous output failed validation. Fix ALL of the following issues and regenerate:",
    )
    if any("truncated" in error for error in errors):
        lines.append(
            "The previous output was truncated. Reduce CSS size and use fewer charts so the full output fits."
        )
    return lines


def _shell_messages(
    title: str,
    summary_md: str,
    sections: list[BriefHtmlSectionChunk],
    language: str,
    errors: list[str],
) -> dict:
    """构造 shell 生成请求上下文；重试时仅附带 shell 侧错误。"""
    content_parts = [f"Report title: {title}"]
    if summary_md:
        content_parts.append(f"Executive Summary Markdown:\n{summary_md}")
    if sections:
        titles = "\n".join(f"- {chunk.section_id} {chunk.title}" for chunk in sections)
        content_parts.append(f"Section titles (in order, for the table of contents):\n{titles}")
    shell_errors = [error for error in errors if not error.startswith("section ")]
    feedback = _error_feedback_lines(shell_errors)
    if feedback:
        content_parts.append("\n".join(feedback))
    return {
        "language": language,
        "messages": [{"role": "user", "content": "\n\n".join(content_parts)}],
    }


def _section_messages(
    chunk: BriefHtmlSectionChunk,
    shell_css: str,
    language: str,
    errors: list[str],
) -> dict:
    """构造章节片段生成请求上下文；重试时附带本章节相关错误。"""
    content_parts = [f"Section Markdown:\n{chunk.markdown}"]
    if shell_css:
        content_parts.append(f"Shell CSS (class vocabulary you MUST reuse):\n{shell_css}")
    section_errors = [
        error
        for error in errors
        if not error.startswith("section ") or error.startswith(f"section {chunk.section_id}:")
    ]
    feedback = _error_feedback_lines(section_errors)
    if feedback:
        content_parts.append(f"You are generating section {chunk.section_id}.\n" + "\n".join(feedback))
    return {
        "language": language,
        "messages": [{"role": "user", "content": "\n\n".join(content_parts)}],
    }


async def _generate_shell(
    llm,
    title: str,
    summary_md: str,
    sections: list[BriefHtmlSectionChunk],
    language: str,
    errors: list[str],
) -> str | None:
    """生成报告 shell（设计系统 + hero/摘要 + TOC + 挂载点）。"""
    response = await ainvoke_llm_with_stats(
        llm,
        apply_system_prompt(
            "brief_html_reporter",
            _shell_messages(title, summary_md, sections, language, errors),
        ),
        agent_name=AgentLlmName.BRIEF_HTML_REPORTER.value,
    )
    raw = str(response.get("content") or "")
    html_body = _extract_html_body(raw)
    if html_body is None:
        logger.warning(
            "[BriefHtmlReporter] No <html_report> block found; raw_chars=%d head=%.200s tail=%.200s.",
            len(raw), raw[:200], raw[-200:],
        )
        return None
    return sanitize_html(html_body)


_HTML_SECTION_BLOCK_RE = re.compile(r"(?s)<html_section>\s*(.*?)\s*</html_section>")


def _extract_section_fragment(raw: str) -> str | None:
    """从 LLM 原始输出提取章节片段。

    优先匹配 ``<html_section>...</html_section>`` 包裹标签；未匹配时兜底
    提取含 ``<h2`` 的裸片段（对齐 shell 的 MVP 式宽松提取）。都失败
    （典型为输出截断）返回 None。
    """
    match = _HTML_SECTION_BLOCK_RE.search(raw)
    if match is not None:
        return match.group(1)
    stripped = re.sub(r"(?m)^\s*```[a-zA-Z]*\s*$", "", raw).strip()
    if "<h2" in stripped.lower():
        return stripped
    return None


def _sanitize_fragment(fragment: str) -> str:
    """清理章节片段：包一层完整文档复用 sanitizer，再取回 body 内容。"""
    wrapped = f"<!DOCTYPE html><html><head></head><body>{fragment}</body></html>"
    cleaned = sanitize_html(wrapped)
    start = cleaned.find("<body>")
    end = cleaned.rfind("</body>")
    if start < 0 or end < 0:
        return ""
    return cleaned[start + len("<body>"):end]


def _placeholder_re_for(chart_id: str) -> re.Pattern[str]:
    """构造匹配指定 data-chart-id 的图表占位元素正则。"""
    return re.compile(
        rf'(?is)<div\b[^>]*data-chart-id="{re.escape(chart_id)}"[^>]*>.*?</div>'
    )


def _extract_fragment_charts(
    fragment: str,
    section_id: str,
) -> tuple[str, list[dict]]:
    """提取章节片段的图表配置并做章节级 id 重命名。

    占位与配置按原 id 配对：成对的占位重命名为 ``s{section_id}-c{k}``（连同
    配置），未配对的占位直接移除（降级为布局无损收缩），未配对的配置丢弃。
    片段内的 ``template#chart-configs`` 一并移除，由拼装阶段统一合并。

    Args:
        fragment: 清理后的章节片段 HTML。
        section_id: 章节编号（用于生成全局唯一图表 id 前缀）。

    Returns:
        ``(去除 template 并重命名占位后的片段, 该章节的图表配置列表)``。

    Raises:
        ValueError: template JSON 非法或 option 校验失败时抛出。
    """
    scanner = _HtmlStructureScanner()
    scanner.feed(fragment)
    scanner.close()
    configs_by_id: dict[str, dict] = {}
    if scanner.chart_configs_raw is not None:
        try:
            parsed = json.loads(scanner.chart_configs_raw)
        except ValueError as exc:
            raise ValueError(f"chart_config: invalid JSON ({exc})") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise ValueError("chart_config: configs must be a JSON array of objects")
        for config in parsed:
            chart_id = config.get("id")
            if not isinstance(chart_id, str) or not _CHART_ID_RE.match(chart_id):
                raise ValueError(f"chart_config: invalid chart id {chart_id!r}")
            error = validate_chart_option(config.get("option"))
            if error:
                raise ValueError(f"chart_config: {error} (chart {chart_id})")
            configs_by_id[chart_id] = config
    result = fragment
    kept_configs: list[dict] = []
    for index, old_id in enumerate(scanner.chart_ids, start=1):
        config = configs_by_id.pop(old_id, None)
        if config is None:
            result = _placeholder_re_for(old_id).sub("", result, count=1)
            logger.warning(
                "[BriefHtmlReporter] Section chart placeholder without config stripped; "
                "section=%s chart_id=%s.",
                section_id, old_id,
            )
            continue
        new_id = f"s{section_id}-c{index}"
        result = _placeholder_re_for(old_id).sub(
            lambda placeholder: placeholder.group(0).replace(
                f'data-chart-id="{old_id}"', f'data-chart-id="{new_id}"'
            ),
            result,
            count=1,
        )
        config["id"] = new_id
        kept_configs.append(config)
    result = _TEMPLATE_BLOCK_RE.sub("", result)
    return result, kept_configs


async def _generate_section_fragments(
    llm,
    shell: str,
    sections: list[BriefHtmlSectionChunk],
    language: str,
    errors: list[str],
) -> tuple[dict[str, tuple[str, list[dict]]], list[str]]:
    """并行生成全部章节片段。

    Args:
        llm: LLM 客户端实例。
        shell: 清理后的报告 shell（提供 CSS 类词汇表）。
        sections: 章节块列表。
        language: 输出语言。
        errors: 上一轮错误列表（作为重试反馈）。

    Returns:
        ``(按 section_id 索引的成功结果, 失败章节错误列表)``；每个成功结果
        包含清理后的 HTML 片段及该章节的图表配置。
    """
    shell_scanner = _HtmlStructureScanner()
    shell_scanner.feed(shell)
    shell_scanner.close()
    shell_css = "\n".join(shell_scanner.style_blocks)

    async def _one(chunk: BriefHtmlSectionChunk) -> tuple[str, list[dict]]:
        response = await ainvoke_llm_with_stats(
            llm,
            apply_system_prompt(
                "brief_html_section",
                _section_messages(chunk, shell_css, language, errors),
            ),
            agent_name=AgentLlmName.BRIEF_HTML_REPORTER.value,
        )
        raw = str(response.get("content") or "")
        fragment = _extract_section_fragment(raw)
        if fragment is None:
            raise ValueError(
                f"section {chunk.section_id}: missing or unclosed <html_section> block "
                "(output likely truncated)"
            )
        sanitized = _sanitize_fragment(fragment)
        if not sanitized.strip():
            raise ValueError(f"section {chunk.section_id}: empty fragment")
        fragment_scanner = _HtmlStructureScanner()
        fragment_scanner.feed(sanitized)
        fragment_scanner.close()
        css_errors = _validate_css_references(fragment_scanner)
        if css_errors:
            # 可归因于单章的安全错误必须在缓存前暴露，确保仅重试失败章节。
            raise ValueError("; ".join(css_errors))
        return _extract_fragment_charts(sanitized, chunk.section_id)

    if not sections:
        return {}, []
    results = await asyncio.gather(
        *(_one(chunk) for chunk in sections),
        return_exceptions=True,
    )
    generated: dict[str, tuple[str, list[dict]]] = {}
    section_errors: list[str] = []
    for chunk, result in zip(sections, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            section_errors.append(f"section {chunk.section_id}: {result}")
            continue
        fragment, chunk_configs = result
        generated[chunk.section_id] = (fragment, chunk_configs)
    return generated, section_errors


_MOUNT_SECTIONS_RE = re.compile(r'(?is)<div id="brief-sections"[^>]*>\s*</div>')
_MOUNT_REFERENCES_RE = re.compile(r'(?is)<div id="brief-references"[^>]*>\s*</div>')


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


def _normalize_chart_configs(
    fragments: list[str], configs: list[dict]
) -> tuple[list[str], list[dict]]:
    """在拼装前应用图表语义兜底并移除空图表占位。

    Args:
        fragments: 已清洗的章节 HTML 片段。
        configs: 已完成 id 配对的 ECharts 配置列表。

    Returns:
        归一化后的片段与配置列表；被完全移除的配置会同步删除其占位元素。
    """
    normalized_configs: list[dict] = []
    for config in configs:
        option = config.get("option")
        if isinstance(option, dict):
            _, warnings = normalize_chart_option(option)
            for warning in warnings:
                logger.warning(
                    "[BriefHtmlReporter] Chart semantic fallback: chart=%s %s.",
                    config.get("id"),
                    warning,
                )
            if isinstance(option.get("series"), list) and not option["series"]:
                chart_id = config.get("id")
                if isinstance(chart_id, str):
                    pattern = _placeholder_re_for(chart_id)
                    fragments = [pattern.sub("", fragment, count=1) for fragment in fragments]
                logger.warning(
                    "[BriefHtmlReporter] Chart semantic fallback: chart=%s has no renderable series; "
                    "chart placeholder removed.",
                    chart_id,
                )
                continue
        normalized_configs.append(config)
    return fragments, normalized_configs


class _BarFillTextNormalizer(HTMLParser):
    """移除 CSS 填充条内部的内容，保留填充条本身及其属性。"""

    def __init__(self) -> None:
        # 输入已经经过白名单清洗；关闭实体转换以避免无关正文被重新编码。
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._bar_fill_depth = 0
        self._bar_fill_had_content = False
        self.normalized_count = 0

    @staticmethod
    def _is_bar_fill(attrs: list) -> bool:
        return any(
            name
            and name.lower() == "class"
            and "bar-fill" in (value or "").split()
            for name, value in attrs
        )

    def handle_starttag(self, tag: str, attrs: list) -> None:
        lowered = tag.lower()
        if self._bar_fill_depth:
            if lowered not in _HTML_VOID_TAGS:
                self._bar_fill_depth += 1
            self._bar_fill_had_content = True
            return
        self.out.append(self.get_starttag_text() or f"<{tag}>")
        if lowered not in _HTML_VOID_TAGS and self._is_bar_fill(attrs):
            self._bar_fill_depth = 1
            self._bar_fill_had_content = False

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if not self._bar_fill_depth:
            self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if self._bar_fill_depth:
            if tag.lower() not in _HTML_VOID_TAGS:
                self._bar_fill_depth -= 1
                if self._bar_fill_depth == 0:
                    if self._bar_fill_had_content:
                        self.normalized_count += 1
                    self._bar_fill_had_content = False
                    self.out.append(f"</{tag}>")
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._bar_fill_depth:
            self._bar_fill_had_content |= bool(data.strip())
        else:
            self.out.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._bar_fill_depth:
            self._bar_fill_had_content = True
        else:
            self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._bar_fill_depth:
            self._bar_fill_had_content = True
        else:
            self.out.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if not self._bar_fill_depth:
            self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if not self._bar_fill_depth:
            self.out.append(f"<!{decl}>")


def normalize_bar_fill_text(html_text: str) -> str:
    """将 ``.bar-fill`` 规范化为纯视觉元素，避免薄条裁切可读文字。"""
    parser = _BarFillTextNormalizer()
    parser.feed(html_text)
    parser.close()
    normalized = "".join(parser.out)
    if parser.normalized_count:
        logger.warning(
            "[BriefHtmlReporter] Removed content from %d CSS bar fill(s); "
            "bar-fill is visual-only.",
            parser.normalized_count,
        )
    return normalized


def _assemble_html_report(
    shell: str,
    fragments: list[str],
    configs: list[dict],
    pre: BriefHtmlPreprocessResult,
    language: str,
) -> str:
    """把 shell、章节片段与图表配置拼装为完整 HTML 文档。

    流程：移除 shell 残留的 chart-configs template → 替换章节/参考文献挂载点 →
    合并配置为单一 template 插入 ``</body>`` 前（内容 HTML 转义，与校验/注入阶段
    的解码一致）。
    """
    fragments, configs = _normalize_chart_configs(fragments, configs)
    shell = _TEMPLATE_BLOCK_RE.sub("", shell)
    sections_html = "\n".join(fragments)
    if _MOUNT_SECTIONS_RE.search(shell):
        html_doc = _MOUNT_SECTIONS_RE.sub(lambda _match: sections_html, shell, count=1)
    else:
        logger.warning("[BriefHtmlReporter] Shell missing #brief-sections mount; insert before </body>.")
        html_doc = _insert_before(shell, "</body>", f"{sections_html}\n")
    references_html = _render_references_html(pre, language)
    if _MOUNT_REFERENCES_RE.search(html_doc):
        html_doc = _MOUNT_REFERENCES_RE.sub(lambda _match: references_html, html_doc, count=1)
    elif references_html:
        html_doc = _insert_before(html_doc, "</body>", f"{references_html}\n")
    if configs:
        payload = html.escape(json.dumps(configs, ensure_ascii=True), quote=False)
        html_doc = _insert_before(
            html_doc, "</body>", f'\n<template id="chart-configs">{payload}</template>\n'
        )
    html_doc = normalize_bar_fill_text(html_doc)
    return convert_inline_citations(html_doc, pre)


async def generate_brief_html_report(*, llm, markdown: str, language: str) -> str:
    """把清洗后的 Brief 报告 md 转写为自包含 HTML，失败带错误重试。

    两阶段并行架构（替代单次整文档生成，输出长度主导耗时）：

    1. shell 生成（1 次调用）：设计系统 CSS + hero/摘要 + TOC + 挂载点；
    2. 章节片段并行生成（N 次调用，共享 shell CSS 词汇表）；
    3. Python 确定性拼装：参考文献渲染、图表 id 重命名与合并、注入。

    重试策略：章节级失败只重生成章节（复用已成功的 shell）；shell 失败
    重生成 shell。重试耗尽时抛 ``ValueError``，由节点层保留已有 Markdown
    产物并写入 warning。

    Args:
        llm: LLM 客户端实例。
        markdown: 组装并完成溯源校验的 Brief 总报告 markdown。
        language: 报告输出语言。

    Returns:
        清理、校验并完成 ECharts/声明注入的自包含 HTML 字符串。

    Raises:
        ValueError: 重试耗尽仍未产出合法 HTML 时抛出。
    """
    pre = preprocess_markdown(markdown)
    title, summary_md, sections = _split_report_markdown(pre.cleaned_markdown)
    max_attempts = max(1, Config().service_config.report_max_generate_retry_num)
    errors: list[str] = []
    shell: str | None = None
    section_cache: dict[str, tuple[str, list[dict]]] = {}
    for attempt_num in range(max_attempts):
        logger.info(
            "[BriefHtmlReporter] Generate html report attempt=%d/%d sections=%d errors=%d.",
            attempt_num + 1, max_attempts, len(sections), len(errors),
        )
        # 上一轮仅有章节错误时复用 shell，避免重复整轮生成。
        reuse_shell = shell is not None and bool(errors) and all(
            error.startswith("section ") for error in errors
        )
        if not reuse_shell:
            # 章节片段复用 shell 的 CSS 词汇表；shell 变化后旧片段必须全部失效。
            section_cache.clear()
            shell = await _generate_shell(llm, title, summary_md, sections, language, errors)
            if shell is None:
                errors = ["shell: missing or unclosed <html_report> tag (output likely truncated)"]
                continue
        pending_sections = [
            chunk for chunk in sections if chunk.section_id not in section_cache
        ]
        generated, section_errors = await _generate_section_fragments(
            llm,
            shell,
            pending_sections,
            language,
            errors,
        )
        section_cache.update(generated)
        if section_errors:
            errors = section_errors
            logger.warning(
                "[BriefHtmlReporter] Section generation failed; attempt=%d/%d errors=%s.",
                attempt_num + 1, max_attempts, errors,
            )
            continue
        fragments = [section_cache[chunk.section_id][0] for chunk in sections]
        configs = [
            config
            for chunk in sections
            for config in section_cache[chunk.section_id][1]
        ]
        assembled = _assemble_html_report(shell, fragments, configs, pre, language)
        errors, warnings = validate_html_report(assembled)
        for warning in warnings:
            logger.warning("[BriefHtmlReporter] Generation warning: %s.", warning)
        if errors:
            logger.warning(
                "[BriefHtmlReporter] Validation failed; attempt=%d/%d errors=%s.",
                attempt_num + 1, max_attempts, errors,
            )
            continue
        html = inject_chart_scripts(assembled)
        html = inject_echarts_library(html)
        html = inject_ai_notice(html, language)
        final_security_assert(html)
        logger.info("[BriefHtmlReporter] Generated html report chars=%d.", len(html))
        return html
    raise ValueError(f"brief html report generation failed: {'; '.join(errors)}")
