# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""网页增强输入的有效性检查与正文清理，不调用模型。"""

from __future__ import annotations

import re
from typing import Any


def partition_primary_content(content: str, title: str = "") -> tuple[str, int]:
    """按标题层级分离有明确论文卡片结构的推荐区，保留正文相关工作。

    Args:
        content: 已清除脚本的 HTML、Markdown 或纯文本正文。
        title: 抓取结果提供的主文章标题，不从 URL 推断。

    Returns:
        主文视图和被排除字符数；无明确结构时原样返回，不根据主题相似度删除。
    """
    reader_title = re.search(r"(?mi)^Title:\s*([^\n]+)", content[:6000])
    title = title or (reader_title[1] if reader_title else "")
    # 仅用于分区视图，保留原抓取正文以便排查；不破坏 p<0.05 等数学表达。
    view = re.sub(
        r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>",
        lambda m: "\n" + "#" * int(m[1]) + " " + re.sub(r"<[^>]+>", "", m[2]) + "\n",
        content, flags=re.I | re.S,
    )
    headings = list(re.finditer(r"(?m)^\s{0,3}(#{1,6})[ \t]+([^\n]+)$", view))
    removed: list[tuple[int, int]] = []
    for index, heading in enumerate(headings):
        label = heading[2].strip().strip(" :：")
        if not re.fullmatch(r"related (?:papers|articles|publications)|recommended (?:papers|articles|reading)|"
                            r"you may also like|相关文章|推荐论文|推荐阅读|相关文献", label, re.I):
            continue
        end = next((h.start() for h in headings[index + 1:] if len(h[1]) <= len(heading[1])), len(view))
        section = view[heading.end():end]
        cards = re.findall(r"(?m)^\s*(?:#{1,6}\s+)?\[[^\]\n]{12,}\]\((https?://[^)\s]+)\)", section)
        cards += re.findall(r"(?mi)^\s*<a\b[^>]*href=['\"](https?://[^'\"]+)['\"][^>]*>[^<\n]{12,}</a>", section)
        if len(set(cards)) >= 2:
            removed.append((heading.start(), end))
    if not removed:
        return content, 0
    primary_start = 0
    if title:
        normalized_title = re.sub(r"\W+", "", title).casefold()
        for heading in headings:
            if len(heading[1]) == 1 and re.sub(r"\W+", "", heading[2]).casefold() == normalized_title:
                primary_start = heading.start()
                break
    pieces: list[str] = []
    cursor = primary_start
    for start, end in removed:
        if end <= cursor:
            continue
        pieces.append(view[cursor:max(cursor, start)])
        cursor = end
    pieces.append(view[cursor:])
    primary = "\n\n".join(piece.strip() for piece in pieces if piece.strip())
    return primary, max(0, len(view) - len(primary))


def prepare_fetched_content(fetched: Any) -> tuple[dict[str, Any], str]:
    """清除脚本并拒绝明确占位页或分区后没有主文的结果。

    Args:
        fetched: 外部抓取结果，合法字典的正文可能是纯文本、HTML 或 Reader Markdown。

    Returns:
        独立结果与固定失败原因；正文中的普通 CAPTCHA 讨论不触发拒绝。
        空输入交给现有长度门槛处理，不改变未解析 PDF 的检查。
    """
    if not isinstance(fetched, dict):
        return {}, "invalid_fetch_result"
    content = str(fetched.get("content") or "")
    if not content.strip():
        return {}, ""
    head = content[:6000]
    titles = [str(fetched.get("title") or "")]
    titles.extend(re.findall(r"(?mi)^Title:\s*([^\n]+)", head))
    titles.extend(re.findall(r"(?is)<title\b[^>]*>(.*?)</title>", head))
    titles.extend(re.findall(r"(?mi)^\s*#{1,2}\s+([^\n]+)", head))
    # 仅匹配整条标题，并要求独立的占位提示；避免文章提及验证码时被误杀。
    blocked_title = any(re.fullmatch(
        r"just a moment[.!… ]*|attention required[! ]*(?:\|\s*cloudflare)?|"
        r"are you a robot\??|access denied|403 forbidden|404 not found|"
        r"sign in to continue|log in to continue|请完成验证|访问被拒绝", title.strip(), re.I,
    ) for title in titles)
    challenge = re.search(
        r"please confirm you are a human|verify (?:that )?you are human|"
        r"complet(?:e|ing) (?:the |a )?captcha|checking your browser|"
        r"you (?:do not|don't) have permission to access|requested (?:url|page) was not found|"
        r"please (?:sign|log) in to (?:continue|access)|请完成.{0,12}验证|没有权限访问", head, re.I,
    )
    if blocked_title and challenge:
        return {}, "placeholder_page"
    cleaned = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "\n", content, flags=re.I | re.S)
    if content.strip() and not cleaned.strip():
        return {}, "no_visible_content"
    primary, excluded = partition_primary_content(cleaned, str(fetched.get("title") or ""))
    if not primary.strip():
        return {}, "no_primary_content"
    # 缓存保存与任务无关的正文分区；原文仍在 content 中，压缩只使用主文视图。
    return {
        **fetched, "content": cleaned,
        "primary_content": primary,
        "excluded_content_chars": excluded,
    }, ""
