# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""DOM 级网页正文噪声过滤器:剔除导航/页脚等 chrome 块,避免污染下游日期判断。

只删高链接密度且命中 chrome 特征词的块,或纯链接汤;占全页文本过半的布局容器永不删除(宁可漏删不可误删正文)。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

#: chrome 特征词(中英)。命中且链接密度达标时判为噪声块;随 badcase 扩充。
CHROME_FEATURE_WORDS = [
    # zh
    "版权所有", "相关阅读", "相关推荐", "上一篇", "下一篇", "网站导航", "ICP备",
    "ICP证", "公网安备", "分享到", "免责声明", "网站地图", "联系我们", "关于我们",
    "微信公众号", "客户端下载", "违法和不良信息举报", "网络110", "增值电信业务",
    "互联网新闻信息服务许可",
    # en
    "All rights reserved", "Related articles", "Related reading", "Sign up",
    "Subscribe", "Cookie", "Privacy Policy", "Terms of Service", "Terms of Use",
    "Follow us", "Share this", "Copyright ©", "Copyright (c)",
]

#: 候选块标签;含 tr 以清理链接汤式表格行(数据行因双条件约束不会被误删)。
BOILERPLATE_CANDIDATE_TAGS = [
    "div", "ul", "li", "p", "section", "aside", "footer", "nav", "tr",
]

#: 规则一阈值:链接密度 ≥ 此值且命中 chrome 特征词时删除。
CHROME_LINK_DENSITY_THRESHOLD = 0.5
#: 规则二阈值:链接密度 ≥ 此值且链接数达标时按纯链接汤删除。
LINK_SOUP_DENSITY_THRESHOLD = 0.85
#: 规则二最低链接数;单链接短行(如下载行)是正文,不按链接汤删。
LINK_SOUP_MIN_LINKS = 3
#: 保险丝:占全页文本超过此比例的布局容器永不删除(只评估其子块)。
LAYOUT_CONTAINER_MAX_TEXT_SHARE = 0.5
#: 过短/过长的块跳过规则评估。
BLOCK_MIN_TEXT_LENGTH = 4
BLOCK_MAX_TEXT_LENGTH = 5000

#: 主文本提取的候选容器选择器,与 harness `_extract_main_text_from_html` 一致。
_MAIN_CANDIDATE_SELECTORS = [
    "main", "[role='main']", "article", ".article", ".article-content",
    ".article-body", ".post", ".post-content", ".entry-content",
    ".content", ".detail", ".news", "#content", "#main",
]


def filter_boilerplate_blocks(soup: BeautifulSoup) -> int:
    """删除满足噪声规则的块,原地修改 soup,返回删除数量。

    规则(任一命中即删):链接密度达标且命中 chrome 特征词,或纯链接汤。
    """
    removed = 0
    removed_ids: set[int] = set()
    total_len = max(len(soup.get_text(" ", strip=True)), 1)

    def has_removed_ancestor(el) -> bool:
        node = el
        while node is not None:
            if id(node) in removed_ids:
                return True
            node = node.parent
        return False

    for el in soup.find_all(BOILERPLATE_CANDIDATE_TAGS):
        if has_removed_ancestor(el):
            continue
        text = el.get_text(" ", strip=True)
        if len(text) < BLOCK_MIN_TEXT_LENGTH or len(text) > BLOCK_MAX_TEXT_LENGTH:
            continue
        # 保险丝:占全页文本过半的布局容器永不删除。
        if len(text) > LAYOUT_CONTAINER_MAX_TEXT_SHARE * total_len:
            continue
        links = el.find_all("a")
        link_len = sum(len(a.get_text(" ", strip=True)) for a in links)
        density = link_len / max(len(text), 1)
        has_chrome = any(word in text for word in CHROME_FEATURE_WORDS)
        if (density >= CHROME_LINK_DENSITY_THRESHOLD and has_chrome) or (
            density >= LINK_SOUP_DENSITY_THRESHOLD and len(links) >= LINK_SOUP_MIN_LINKS
        ):
            removed_ids.add(id(el))
            el.decompose()
            removed += 1
    return removed


def _extract_main_text(soup: BeautifulSoup) -> str:
    """从已过滤 DOM 提取主文本;无候选容器时退化为 p/li/h 拼接,再退化为 body 全文。"""
    for selector in ("script", "style", "noscript", "svg", "canvas", "iframe"):
        for node in soup.select(selector):
            node.decompose()
    for selector in (
        "nav", "header", "footer", "aside", "form", "button",
        "[role='navigation']", ".nav", ".navbar", ".header", ".footer",
        ".sidebar", ".aside", ".recommend", ".related", ".share",
        ".breadcrumb", ".menu", ".toolbar",
    ):
        for node in soup.select(selector):
            node.decompose()

    best_text = ""
    for selector in _MAIN_CANDIDATE_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        node_text = node.get_text("\n", strip=True)
        if len(node_text) > len(best_text):
            best_text = node_text
    if not best_text:
        body = soup.body or soup
        blocks = []
        for node in body.select("p, li, h1, h2, h3"):
            piece = node.get_text(" ", strip=True)
            if len(piece) >= 20:
                blocks.append(piece)
        best_text = "\n".join(blocks) if blocks else body.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", best_text).strip()


def extract_clean_main_text(html: str) -> str:
    """从原始 HTML 提取净化后的正文(先 DOM 规则过滤,再取主文本);无法解析返回空串。"""
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    filter_boilerplate_blocks(soup)
    return _extract_main_text(soup)


def detect_boilerplate(text: str) -> bool:
    """判断已提取纯文本是否仍含 chrome 污染(命中特征词即判污染)。

    供 harness 直连结果取舍:仅判定污染时才考虑用净化输出替换。
    已知边界:不含特征词的导航菜单行(如"党建工作/会员之家")检测不到。
    """
    if not text:
        return False
    return any(word in text for word in CHROME_FEATURE_WORDS)
