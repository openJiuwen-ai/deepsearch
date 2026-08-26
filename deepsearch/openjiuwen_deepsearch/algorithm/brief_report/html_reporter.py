"""Brief 报告的自包含 HTML 生成：清洗、清理、校验与确定性脚本注入。"""

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName


logger = logging.getLogger(__name__)


@dataclass
class BriefHtmlPreprocessResult:
    """预处理清洗后的 markdown 与引用元数据。

    Attributes:
        cleaned_markdown: 行内引用标记已清洗为 ``[n]`` 的 markdown 正文。
        inline_citation_numbers: 正文内出现的引用编号序列（按出现顺序）。
        reference_entries: 规范化后的参考文献条目 ``(编号, 标题, URL)``，按编号升序。
    """

    cleaned_markdown: str
    inline_citation_numbers: list[int] = field(default_factory=list)
    reference_entries: list[tuple[int, str, str]] = field(default_factory=list)


_CHECKED_CITATION_RE = re.compile(r"\[checked_citation:[^\]]*\]\[\[(?P<num>\d+)\]\]\(")
_SOURCE_TRACER_RE = re.compile(r"(?P<image>!)?\[source_tracer_result\]\[(?P<title>[^\]]*)\]\(")
_ENTRY_LINE_RE = re.compile(r"(?m)^\[(?P<num>\d+)\]\.\s*\[(?P<title>[^\]]*)\]\(")


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
    """把行内引用标记清洗为 [n]，并规范化文末参考文献条目。

    兼容两种输入形态：溯源校验后的 ``[checked_citation:<id>][[n]](URL)`` 与
    校验跳过/异常时回退的 ``[source_tracer_result][标题](URL)``；图片引用
    ``![source_tracer_result][标题](URL)``（``!`` 前缀随匹配一起消除）按
    文本引用统一处理。同一 URL 复用编号。

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
    inline_numbers: list[int] = []
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
        parts.append(f"[{number}]")
        inline_numbers.append(number)
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
        inline_citation_numbers=inline_numbers,
        reference_entries=[(n, merged[n][0], merged[n][1]) for n in sorted(merged)],
    )


_ALLOWED_TAGS = frozenset({
    "html", "head", "body", "title", "style", "meta",
    "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
    "strong", "em", "b", "i", "blockquote", "sup", "sub", "a",
    "template", "section", "footer", "header", "nav", "hr", "br",
})
_VOID_TAGS = frozenset({"br", "hr", "meta"})
_HTML_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_ALLOWED_ATTRS = frozenset({"class", "id", "style", "data-chart-id", "title", "href"})
_CHART_CONFIGS_TEMPLATE_ID = "chart-configs"


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
                # 页内锚点（#ref-n）无外联/脚本风险，与外链一并放行，
                # 供行内引用上标跳转到文末参考文献条目。
                if not stripped.startswith(("http://", "https://", "#")):
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
    parser.feed(html_text)
    parser.close()
    parser.finalize()
    return "".join(parser.out)


_SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)
_ON_ATTR_RE = re.compile(r"\son[a-zA-Z]+\s*=", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"javascript:", re.IGNORECASE)
_CHART_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_ESCAPE_RE = re.compile(r"\\([0-9A-Fa-f]{1,6})\s?")
_FORBIDDEN_URL_PAYLOADS = ("http://", "https://", "image://", "data:", "javascript:")


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


class _HtmlStructureScanner(HTMLParser):
    """收集正文文本、sup 序列、链接、图表占位、配置与 CSS。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.sup_texts: list[str] = []
        self.hrefs: list[str] = []
        self.chart_ids: list[str] = []
        self.chart_configs_raw: str | None = None
        self.style_blocks: list[str] = []
        self.inline_styles: list[str] = []
        self._in_sup = 0
        self._in_style = 0
        self._in_template_configs = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_map = {name: value for name, value in attrs if name}
        if tag == "sup":
            self._in_sup += 1
        elif tag == "a" and attr_map.get("href"):
            self.hrefs.append(attr_map["href"])
        elif attr_map.get("data-chart-id"):
            self.chart_ids.append(attr_map["data-chart-id"])
        elif tag == "template" and attr_map.get("id") == _CHART_CONFIGS_TEMPLATE_ID:
            self._in_template_configs += 1
        elif tag == "style":
            self._in_style += 1
        if attr_map.get("style"):
            self.inline_styles.append(attr_map["style"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup" and self._in_sup:
            self._in_sup -= 1
        elif tag == "template" and self._in_template_configs:
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
        if self._in_sup:
            self.sup_texts.append(data)
        self.text_parts.append(data)


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


def _validate_section_headings(plain_text: str, cleaned_markdown: str) -> list[str]:
    """校验 md 的 #~### 标题按顺序（含层级）出现在 HTML 纯文本中。"""
    headings = [
        match.group(2).strip()
        for match in re.finditer(r"(?m)^(#{1,3})\s+(.+)$", cleaned_markdown)
    ]
    if not headings:
        return []
    text = " ".join(plain_text.split())
    cursor = 0
    for title in headings:
        normalized = " ".join(title.split())
        position = text.find(normalized, cursor)
        if position < 0:
            return [f"missing_section_heading: {title[:60]}"]
        cursor = position + len(normalized)
    return []


def _validate_citations(scanner: _HtmlStructureScanner, pre: BriefHtmlPreprocessResult) -> list[str]:
    """校验引用完整性：[n] 无残留、sup 编号集合覆盖、文献 URL 全部出现。

    sup 校验放宽为集合语义：要求 markdown 里的每个引用编号在 HTML 中至少
    出现一次，且 HTML 不得出现 markdown 之外的编号（防幻觉引用）；忽略
    重复编号的次数差异与顺序——长报告转写时 LLM 偶发漏转个别重复上标
    不应导致整次产物被拒。
    """
    errors: list[str] = []
    if not pre.inline_citation_numbers:
        return errors
    text = "".join(scanner.text_parts)
    if re.search(r"\[\d+\]", text):
        errors.append("raw_inline_citation_marker_left")
    sup_numbers = [
        int(number)
        for sup_text in scanner.sup_texts
        for number in re.findall(r"\d+", sup_text)
    ]
    expected_set = set(pre.inline_citation_numbers)
    actual_set = set(sup_numbers)
    unknown = sorted(actual_set - expected_set)
    if unknown:
        errors.append(f"sup_citation_unknown_numbers: {unknown}")
    missing_numbers = sorted(expected_set - actual_set)
    if missing_numbers:
        errors.append(f"sup_citation_missing_numbers: {missing_numbers}")
    html_urls = set(scanner.hrefs)
    missing_urls = sorted(
        {url for _, _, url in pre.reference_entries if url and url not in html_urls}
    )
    if missing_urls:
        errors.append(f"missing_reference_entries: {missing_urls[:3]}")
    return errors


def validate_html_report(html: str, pre: BriefHtmlPreprocessResult) -> tuple[list[str], list[str]]:
    """对清理后的 HTML 执行安全硬校验，内容保真降级为警告。

    校验分两级（对齐 report_full_html MVP 的成功率策略）：

    - **errors（硬校验）**：结构闭合、script/事件属性/危险 URL、CSS 外链、
      图表占位与配置一致性。违反安全契约必须重试。
    - **warnings（保真类）**：章节标题缺失、sup 引用编号缺失/越界、
      文献 URL 缺失、``[n]`` 残留。长输出转写偶发遗漏只记录警告，
      不触发重试拒绝——保真缺陷不损害安全性，重试成本（约 3 分钟/次）
      远高于其价值。

    Args:
        html: 清理后的完整 HTML 文档。
        pre: 预处理清洗结果（提供行内引用序列与参考文献条目）。

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
    if _ON_ATTR_RE.search(html):
        errors.append("event_attribute_present")
    if _JAVASCRIPT_URL_RE.search(html):
        errors.append("javascript_url_present")

    scanner = _HtmlStructureScanner()
    scanner.feed(html)
    scanner.close()
    errors.extend(_validate_css_references(scanner))
    chart_errors, chart_warnings = _validate_chart_configs(scanner)
    errors.extend(chart_errors)
    warnings.extend(chart_warnings)
    warnings.extend(_validate_section_headings("".join(scanner.text_parts), pre.cleaned_markdown))
    warnings.extend(_validate_citations(scanner, pre))
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
_TEMPLATE_BLOCK_RE = re.compile(r"(?s)<template id=\"chart-configs\">(.*?)</template>")
# 图表占位元素（prompt 约定为空内容 div，class 必含 echarts-chart）；
# template 缺失时的降级移除依赖它。
_CHART_PLACEHOLDER_RE = re.compile(
    r'(?is)<div\b[^>]*class="[^"]*echarts-chart[^"]*"[^>]*>.*?</div>'
)
_MARKED_BLOCK_RE = re.compile(
    r"(?s)<!--openjiuwen:(?:echarts-lib|chart-init)-->.*?<!--/openjiuwen:(?:echarts-lib|chart-init)-->"
)


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
    """把通过 SHA-256 校验的 echarts.min.js 以内联脚本注入 head。

    Args:
        html: 清理后的 HTML 文档。

    Returns:
        head（或 body 顶部兜底）内联 ECharts 库脚本的 HTML 文档。
    """
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
        language: 报告语言（zh-CN / en-US）。

    Returns:
        body 末尾带 AI 生成声明 footer 的 HTML 文档。
    """
    text = (
        "This research report was generated by AI and is for reference only."
        if language == "en-US"
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
    if _ON_ATTR_RE.search(residual):
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


def _html_reporter_messages(
    cleaned_markdown: str,
    language: str,
    errors: list[str],
    truncated: bool,
) -> dict:
    """构造 HTML 转写请求上下文；重试时附带上一轮校验错误。

    Args:
        cleaned_markdown: 预处理清洗后的报告 markdown。
        language: 输出语言。
        errors: 上一轮校验错误列表；首轮为空。
        truncated: 上一轮是否因未闭合标签判定为截断。

    Returns:
        供 ``apply_system_prompt`` 使用的上下文 payload。
    """
    content_parts = [f"Report Markdown:\n{cleaned_markdown}"]
    if errors:
        lines = []
        for error in errors:
            lines.append(f"- {error}")
            if error.startswith("chart_config"):
                # 机器错误码对 LLM 可执行性差，附上双路径修复指引：
                # 占位与配置必须成对出现，二选一修复。
                lines.append(
                    "  Fix: ECharts placeholders (<div class=\"echarts-chart\" data-chart-id=\"...\">) and "
                    "the config block (<template id=\"chart-configs\">[...]</template> just before </body>) "
                    "MUST appear in pairs with matching ids. Either remove ALL placeholder divs and render "
                    "those charts as CSS bar rows instead, or add/fix the template block so every "
                    "placeholder has one config entry with the same id."
                )
        content_parts.append(
            "Your previous output failed validation. Fix ALL of the following issues "
            f"and regenerate the complete HTML report:\n" + "\n".join(lines)
        )
        if truncated:
            content_parts.append(
                "The previous output was truncated. Reduce CSS size and use fewer charts "
                "so the full document fits."
            )
    return {
        "language": language,
        "messages": [{"role": "user", "content": "\n\n".join(content_parts)}],
    }


async def generate_brief_html_report(*, llm, markdown: str, language: str) -> str:
    """把清洗后的 Brief 报告 md 转写为自包含 HTML，失败带错误重试。

    重试耗尽时抛 ``ValueError``，由节点层转为 ``REPORT_GENERATE_ERROR``。

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
    max_attempts = max(1, Config().service_config.report_max_generate_retry_num)
    errors: list[str] = []
    truncated = False
    for attempt_num in range(max_attempts):
        logger.info(
            "[BriefHtmlReporter] Generate html report attempt=%d/%d errors=%d.",
            attempt_num + 1, max_attempts, len(errors),
        )
        response = await ainvoke_llm_with_stats(
            llm,
            apply_system_prompt(
                "brief_html_reporter",
                _html_reporter_messages(pre.cleaned_markdown, language, errors, truncated),
            ),
            agent_name=AgentLlmName.BRIEF_HTML_REPORTER.value,
        )
        raw = str(response.get("content") or "")
        html_body = _extract_html_body(raw)
        if html_body is None:
            errors = ["missing or unclosed <html_report> tag (output likely truncated)"]
            truncated = True
            logger.warning(
                "[BriefHtmlReporter] No <html_report> block found; raw_chars=%d head=%.200s tail=%.200s.",
                len(raw), raw[:200], raw[-200:],
            )
            continue
        sanitized = sanitize_html(html_body)
        errors, warnings = validate_html_report(sanitized, pre)
        for warning in warnings:
            # 保真类缺陷（漏标题/漏引用上标等）不触发重试：安全校验已通过，
            # 重试一次约 3 分钟且大概率引入新的转写遗漏，性价比过低。
            logger.warning("[BriefHtmlReporter] Content fidelity warning: %s.", warning)
        if errors:
            truncated = False
            logger.warning(
                "[BriefHtmlReporter] Validation failed; attempt=%d/%d raw_chars=%d errors=%s.",
                attempt_num + 1, max_attempts, len(raw), errors,
            )
            continue
        html = inject_chart_scripts(sanitized)
        html = inject_echarts_library(html)
        html = inject_ai_notice(html, language)
        final_security_assert(html)
        logger.info("[BriefHtmlReporter] Generated html report chars=%d.", len(html))
        return html
    raise ValueError(f"brief html report generation failed: {'; '.join(errors)}")
