"""Brief HTML 报告的白名单清理、结构扫描与安全校验。"""

import html
import re
from html.parser import HTMLParser


_ALLOWED_TAGS = frozenset({
    "html", "head", "body", "title", "style", "meta",
    "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
    "strong", "em", "b", "i", "blockquote", "sup", "sub", "a",
    "template", "section", "footer", "header", "nav", "hr", "br",
    "main", "article", "aside", "figure", "figcaption", "details", "summary",
})
_VOID_TAGS = frozenset({"br", "hr", "meta"})
_HTML_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_ALLOWED_ATTRS = frozenset({
    "class", "id", "style", "data-chart-id", "title", "href", "target", "rel",
})
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
_SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"javascript:", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_ESCAPE_RE = re.compile(r"\\([0-9A-Fa-f]{1,6})\s?")
_MARKED_BLOCK_RE = re.compile(
    r"(?s)<!--openjiuwen:(?:echarts-lib|chart-init)-->.*?<!--/openjiuwen:(?:echarts-lib|chart-init)-->"
)


def _json_brackets_balanced(text: str) -> bool:
    """判断 JSON 集合括号（含字符串感知）是否平衡闭合。"""
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
    """把 chart-configs 的原始 JSON 载荷转义为 HTML 文本节点。"""
    parts: list[str] = []
    cursor = 0
    match = _CHART_CONFIGS_OPEN_TAG_RE.search(html_text, cursor)
    while match is not None:
        parts.append(html_text[cursor:match.end()])
        payload_start = match.end()
        closing = None
        for candidate in _CHART_CONFIGS_CLOSE_TAG_RE.finditer(html_text, payload_start):
            raw_payload = html_text[payload_start:candidate.start()]
            if _json_brackets_balanced(html.unescape(raw_payload)):
                closing = candidate
                break
        if closing is None:
            parts.append(html.escape(html.unescape(html_text[payload_start:]), quote=False))
            cursor = len(html_text)
            break
        payload = html.unescape(html_text[payload_start:closing.start()])
        payload = _CHART_CONFIGS_FORBIDDEN_TAG_BLOCK_RE.sub("", payload)
        payload = _CHART_CONFIGS_INNER_CLOSE_TAG_RE.sub("", payload)
        parts.append(html.escape(payload, quote=False))
        parts.append(closing.group(0))
        cursor = closing.end()
        match = _CHART_CONFIGS_OPEN_TAG_RE.search(html_text, cursor)
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
            if tag not in _HTML_VOID_TAGS:
                self._chart_drop_depth += 1
            return
        if tag not in _ALLOWED_TAGS:
            if tag in _HTML_VOID_TAGS:
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
    """按白名单清理 LLM 输出的 HTML 并重新序列化完整文档。"""
    parser = _Sanitizer()
    parser.feed(_escape_chart_config_template_payloads(html_text))
    parser.close()
    parser.finalize()
    return "".join(parser.out)


class _EventAttributeScanner(HTMLParser):
    """检测实际 HTML 元素属性中的事件处理器。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_event_attribute = False

    def handle_starttag(self, _tag: str, attrs: list) -> None:
        self.has_event_attribute |= any(
            name and name.lower().startswith("on") for name, _value in attrs
        )

    handle_startendtag = handle_starttag


def _contains_event_attribute(html_text: str) -> bool:
    """判断 HTML 中是否存在实际元素事件属性。"""
    scanner = _EventAttributeScanner()
    scanner.feed(html_text)
    scanner.close()
    return scanner.has_event_attribute


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
        if tag == "style":
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
        if self._in_template_configs:
            self.chart_configs_raw = (self.chart_configs_raw or "") + data


def _decode_css_escape(match: re.Match) -> str:
    try:
        return chr(int(match.group(1), 16))
    except (ValueError, OverflowError):
        return ""


def _normalize_css(css: str) -> str:
    css = _CSS_COMMENT_RE.sub("", css)
    css = _CSS_ESCAPE_RE.sub(_decode_css_escape, css)
    return re.sub(r"\s+", "", css).lower()


def _css_has_external_reference(css: str) -> bool:
    """检测 CSS 中的 url() 与 @import 外部引用。"""
    normalized = _normalize_css(css)
    return "url(" in normalized or "@import" in normalized


def _validate_css_references(scanner: _HtmlStructureScanner) -> list[str]:
    """校验 style 块与内联 style 属性均不含外部引用。"""
    for css in [*scanner.style_blocks, *scanner.inline_styles]:
        if css and _css_has_external_reference(css):
            return ["css_external_reference"]
    return []


def _insert_before(html_text: str, closing_tag: str, payload: str) -> str:
    """把 payload 插入到 closing_tag 最后一次出现处之前。"""
    index = html_text.rfind(closing_tag)
    if index < 0:
        return html_text
    return html_text[:index] + payload + html_text[index:]


def final_security_assert(html_text: str) -> None:
    """终检断言：除系统注入脚本外，产物无脚本、事件或外部引用。"""
    residual = _MARKED_BLOCK_RE.sub("", html_text)
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
        raise RuntimeError(f"brief html final security check failed: {'; '.join(problems)}")
