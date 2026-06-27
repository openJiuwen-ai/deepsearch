# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Markdown URL 边界解析工具。"""


def find_markdown_url_end(text: str, open_paren_index: int) -> int | None:
    """从 Markdown 链接左括号开始扫描匹配的右括号。

    Args:
        text: 待扫描文本。
        open_paren_index: URL 左括号 ``(`` 在文本中的偏移。

    Returns:
        匹配成功时返回右括号后一位偏移；未闭合或起点非法时返回 ``None``。
    """
    if open_paren_index < 0 or open_paren_index >= len(text) or text[open_paren_index] != "(":
        return None

    depth = 1
    index = open_paren_index + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            # Markdown URL 里允许转义括号；被转义的字符不参与括号深度计算。
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def extract_markdown_url(text: str, open_paren_index: int) -> tuple[str, int] | None:
    """提取 Markdown URL 及其结束偏移。

    Args:
        text: 待扫描文本。
        open_paren_index: URL 左括号 ``(`` 在文本中的偏移。

    Returns:
        ``(url, end_offset)``；未找到闭合右括号时返回 ``None``。
    """
    end = find_markdown_url_end(text, open_paren_index)
    if end is None:
        return None
    return text[open_paren_index + 1:end - 1], end
