# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""DOM 级网页正文噪声过滤器(导航/页脚/chrome 块剔除)。

背景:harness 直连抓取(`WebFetchWebpageAdapter.fetch_webpage_sync`)返回的
已提取正文可能混入导航菜单、页脚备案号等 chrome 内容(例如协会官网菜单里的
"纲要 2026—2035年"),污染下游 LLM 对文档时间的判断。

本模块移植自实验验证的最优方案(`.worktrees/temporal-v2/experiments/
content_extraction/REPORT.md` §1 方法 4 / §7 最终推荐):"类 harness 选择器
提取 + DOM 级规则前置过滤"。在 88 篇对照语料上实测关键标记串 36/36 零误杀,
chrome 残留行占比 p90 从 0.81% 降至 0.19%。

设计原则:宁可漏杀不可错杀。所有删除规则都要求"链接密度 AND 特征词"双条件
或"极高链接密度 AND 多链接"的纯链接汤,且占全页文本过半的布局容器永不删除。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

#: chrome 特征词表(中英双语)。命中任一词且链接密度达标时判定为噪声块。
#: 词表依据 experiments/content_extraction/REPORT.md §1 推荐参数,
#: 随 badcase 积累可扩充(报告 §7 补充建议)。
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

#: 参与规则评估的块级标签。与实验实现保持一致;`tr` 在内以便清理链接汤式表格行,
#: 数据行(短、数字多、无链接、无特征词)因双条件约束不会被误删。
BOILERPLATE_CANDIDATE_TAGS = [
    "div", "ul", "li", "p", "section", "aside", "footer", "nav", "tr",
]

#: 规则一:链接密度达到该值且命中 chrome 特征词时删除(REPORT.md §1)。
CHROME_LINK_DENSITY_THRESHOLD = 0.5
#: 规则二:链接密度达到该值且链接数达标时按纯链接汤删除(REPORT.md §1)。
LINK_SOUP_DENSITY_THRESHOLD = 0.85
#: 规则二的最低链接数;单链接短行(如"…报告.pdf"下载行)是正文而非链接汤。
LINK_SOUP_MIN_LINKS = 3
#: 保险丝:文本量占全页文本超过该比例的布局容器永不删除,只评估其内部子块。
LAYOUT_CONTAINER_MAX_TEXT_SHARE = 0.5
#: 过短/过长的块不参与规则评估(过短无判定意义,过长多为正文容器)。
BLOCK_MIN_TEXT_LENGTH = 4
BLOCK_MAX_TEXT_LENGTH = 5000

#: 主文本提取的候选容器选择器,与 harness `_extract_main_text_from_html` 一致。
_MAIN_CANDIDATE_SELECTORS = [
    "main", "[role='main']", "article", ".article", ".article-content",
    ".article-body", ".post", ".post-content", ".entry-content",
    ".content", ".detail", ".news", "#content", "#main",
]


def filter_boilerplate_blocks(soup: BeautifulSoup) -> int:
    """在 DOM 上删除满足噪声规则的块(原地修改)。

    删除规则(任一命中即删):
        1. 链接密度 >= ``CHROME_LINK_DENSITY_THRESHOLD`` 且命中 chrome 特征词;
        2. 链接密度 >= ``LINK_SOUP_DENSITY_THRESHOLD`` 且链接数
           >= ``LINK_SOUP_MIN_LINKS``(纯链接汤)。

    Args:
        soup: 待过滤的 BeautifulSoup 文档树,会被原地修改。

    Returns:
        被删除的块数量。
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
        # 保险丝:占全页文本过半的布局容器永不删除,宁可漏杀不可错杀。
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
    """按 harness 候选选择器思路从(已过滤的)DOM 提取主文本。

    Args:
        soup: 已完成噪声过滤的 BeautifulSoup 文档树。

    Returns:
        提取出的主文本;无候选容器时退化为 p/li/h 块拼接,再退化为 body 全文。
    """
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
    """从原始 HTML 提取经 DOM 规则过滤后的干净正文。

    流程:DOM 规则过滤(链接密度 + chrome 特征词)-> 语义化标签剔除 ->
    候选选择器取最长主文本。不依赖 harness 包内部函数。

    Args:
        html: 原始网页 HTML 字符串。

    Returns:
        净化后的正文文本;输入无法解析时返回空串。
    """
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    filter_boilerplate_blocks(soup)
    return _extract_main_text(soup)


def detect_boilerplate(text: str) -> bool:
    """判断已提取的纯文本是否仍含 chrome 污染(命中特征词)。

    用于对 harness 直连结果做取舍:仅当判定污染时才考虑用净化输出替换。
    与实验的 chrome 残留行指标同口径(特征词命中),已知边界:不含特征词的
    导航菜单行(如"党建工作/会员之家")检测不到,见 REPORT.md §2 读表注意。

    Args:
        text: 已提取的网页正文纯文本。

    Returns:
        任一 chrome 特征词出现在文本中时返回 True。
    """
    if not text:
        return False
    return any(word in text for word in CHROME_FEATURE_WORDS)
