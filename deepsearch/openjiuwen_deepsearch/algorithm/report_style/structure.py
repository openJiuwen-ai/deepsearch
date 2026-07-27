# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Semantic DOM decoration for exported report HTML."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag


SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg\s*>", flags=re.IGNORECASE | re.DOTALL)
HEADING_TAG_NAMES = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def _protect_svg_markup(html: str) -> tuple[str, dict[str, str]]:
    """在 DOM 装饰期间保留 SVG 的原始标记。

    Args:
        html: 待装饰的完整 HTML 文档。

    Returns:
        tuple[str, dict[str, str]]: 替换 SVG 后的 HTML 及占位符到原始 SVG 的映射。
    """
    protected_svg: dict[str, str] = {}
    placeholder_index = 0

    def _replace(match: re.Match[str]) -> str:
        """用确定性注释占位符替换一个 SVG 块。

        Args:
            match: 当前匹配到的 SVG 标记。

        Returns:
            str: 可在序列化后恢复的 SVG 注释占位符。
        """
        nonlocal placeholder_index
        # 原报告可能含有占位符样式的注释；只使用原文中不存在的值，恢复时才不会误替换它。
        while True:
            placeholder = f"<!--REPORT_SVG_{placeholder_index}-->"
            placeholder_index += 1
            if placeholder not in html:
                break
        protected_svg[placeholder] = match.group(0)
        return placeholder

    return SVG_BLOCK_RE.sub(_replace, html), protected_svg


def _append_class(element: Tag, class_name: str) -> None:
    """向元素追加一个不重复的 CSS 类。

    Args:
        element: 需要标记的 HTML 元素。
        class_name: 要追加的 CSS 类名。

    Returns:
        None.
    """
    classes = element.get("class", [])
    if class_name not in classes:
        element["class"] = [*classes, class_name]


def _is_abstract_heading(element: Tag) -> bool:
    """判断任意级标题是否表示报告摘要。

    Args:
        element: 待判断的标题元素。

    Returns:
        bool: 标题为“摘要”或“Abstract”时返回 True。
    """
    return element.get_text(" ", strip=True).casefold() in {"摘要", "abstract"}


def _is_heading(element: object) -> bool:
    """判断节点是否为 HTML 标题元素。

    Args:
        element: 待判断的 DOM 节点。

    Returns:
        bool: 节点是 h1 至 h6 标题时返回 True。
    """
    return isinstance(element, Tag) and element.name in HEADING_TAG_NAMES


def _heading_level(element: Tag) -> int:
    """返回标题节点的层级数值。

    Args:
        element: 已确认属于 h1 至 h6 的标题节点。

    Returns:
        int: 标题层级，h1 返回 1，h6 返回 6。
    """
    return int(element.name[1])


def _is_non_whitespace_content_block(element: object) -> bool:
    """判断节点是否可作为无标题报告的封面内容块。

    Args:
        element: 待判断的 DOM 节点。

    Returns:
        bool: 节点是非脚本、非样式标签，或包含非空白文本时返回 True。
    """
    if isinstance(element, Tag):
        return element.name not in {"script", "style"}
    return isinstance(element, NavigableString) and bool(element.strip())


def _mark_data_blocks(soup: BeautifulSoup) -> None:
    """为图表和表格补充供主题 CSS 使用的语义类。

    Args:
        soup: 已解析的完整 HTML 文档。

    Returns:
        None.
    """
    for figure in soup.find_all("figure"):
        _append_class(figure, "report-figure")

    for mermaid_wrap in soup.select(".mermaid-wrap"):
        _append_class(mermaid_wrap, "report-figure")

    for image in soup.find_all("img"):
        parent = image.parent
        # Markdown 图片通常独占一个段落；给段落打标记可让图注保持相邻，且不改写资源元素。
        if isinstance(parent, Tag) and parent.name == "p":
            _append_class(parent, "report-figure")
        elif isinstance(parent, Tag) and parent.name != "body":
            _append_class(parent, "report-figure")

    for table in soup.find_all("table"):
        wrapper = table.find_parent("div", class_="table-wrap")
        _append_class(wrapper if wrapper is not None else table, "report-table")


def decorate_report_html(html: str) -> str:
    """为完整报告 HTML 增加稳定的桌面报告语义结构。

    第一个 h1 至 h6 标题（包括“摘要”或“Abstract”）成为封面，并以其标题
    层级识别后续章节；没有标题时，第一个非空白内容块成为封面。仅首个标题之后
    的“摘要”或“Abstract”标题会形成摘要区。该过程只重组容器和追加 CSS 类，
    保留已有的文本、链接、SVG 内容及资源路径。

    Args:
        html: Markdown 导出器生成的完整 HTML 文档。

    Returns:
        str: 带报告页面、内容区、章节及数据块语义类的完整 HTML。
    """
    protected_html, protected_svg = _protect_svg_markup(html)
    soup = BeautifulSoup(protected_html, "html.parser")
    body = soup.body
    if body is None:
        body = soup.new_tag("body")
        if soup.html is None:
            document = soup.new_tag("html")
            for node in list(soup.contents):
                document.append(node.extract())
            soup.append(document)
        soup.html.append(body)

    _append_class(body, "report-page")
    existing_shell = body.find("main", class_="report-shell", recursive=False)
    if existing_shell is not None:
        _mark_data_blocks(soup)
        result = str(soup)
        for placeholder, svg_markup in protected_svg.items():
            result = result.replace(placeholder, svg_markup)
        return result

    shell = soup.new_tag("main", attrs={"class": "report-shell"})
    cover = soup.new_tag("header", attrs={"class": "report-cover"})
    # 封面始终放在 shell 的首位；这样正文在标题前时也不会破坏视觉系统的封面位置。
    shell.append(cover)
    content: Tag | None = None
    current_container: Tag | None = None
    unsectioned_content: Tag | None = None

    body_nodes = list(body.contents)
    cover_node = next((node for node in body_nodes if _is_heading(node)), None)
    if cover_node is None:
        cover_node = next(
            (node for node in body_nodes if _is_non_whitespace_content_block(node)), None
        )
    cover_heading_level = _heading_level(cover_node) if _is_heading(cover_node) else None

    def get_content() -> Tag:
        """按需创建并返回正文容器。

        Returns:
            Tag: 章节与非标题正文的容器元素。
        """
        nonlocal content
        if content is None:
            content = soup.new_tag("div", attrs={"class": "report-content"})
            shell.append(content)
        return content

    def get_unsectioned_content() -> Tag:
        """按需创建并返回未被标题开启的稳定正文章节。

        Returns:
            Tag: 包装普通顶层正文的章节元素。
        """
        nonlocal unsectioned_content
        if unsectioned_content is None:
            unsectioned_content = soup.new_tag("section", attrs={"class": "report-section"})
            get_content().append(unsectioned_content)
        return unsectioned_content

    for node in body_nodes:
        if isinstance(node, Tag) and node.name in {"script", "style"}:
            continue
        node.extract()

        if node is cover_node:
            cover.append(node)
            current_container = None
            continue

        if isinstance(node, NavigableString) and not node.strip():
            # 模板空白不是正文内容，保留它但不创建章节，避免改变报告文本数据。
            shell.append(node)
        elif _is_heading(node) and _is_abstract_heading(node):
            abstract = soup.new_tag("section", attrs={"class": "report-abstract"})
            abstract.append(node)
            shell.append(abstract)
            current_container = abstract
        elif _is_heading(node) and _heading_level(node) == cover_heading_level:
            section = soup.new_tag("section", attrs={"class": "report-section"})
            section.append(node)
            get_content().append(section)
            current_container = section
        elif current_container is not None:
            current_container.append(node)
        else:
            get_unsectioned_content().append(node)

    if content is None:
        get_content()
    if content.find("section", class_="report-section", recursive=False) is None:
        # 封面可能消费全部源内容；仍提供稳定的章节锚点供主题 CSS 和导出消费者使用。
        get_unsectioned_content()
    body.append(shell)
    _mark_data_blocks(soup)
    result = str(soup)
    for placeholder, svg_markup in protected_svg.items():
        result = result.replace(placeholder, svg_markup)
    return result
