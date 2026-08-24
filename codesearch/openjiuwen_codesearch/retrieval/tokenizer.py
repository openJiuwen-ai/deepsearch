# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""代码文本分词工具（纯函数，无 Milvus 依赖）。"""

import re


def tokenise_code_string(text: str) -> str:
    """camelCase / PascalCase / snake_case → 空格分隔小写词。"""
    if not text:
        return ""
    s = re.sub(r"(_|(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z]))", " ", text)
    return " ".join(s.split()).lower()


def generate_char_trigrams(text: str, max_chars: int = 65535) -> str:
    """字符三元组 hex 编码，空格连接；按目标字段长度上限精确截断。"""
    if not text:
        return ""
    if len(text) < 3:
        return text.encode("utf-8").hex()[:max_chars]

    trigrams = [text[i:i + 3].encode("utf-8").hex() for i in range(len(text) - 2)]
    res_list: list[str] = []
    current_len = 0
    for t in trigrams:
        add_len = len(t) + (1 if current_len > 0 else 0)
        if current_len + add_len > max_chars:
            break
        res_list.append(t)
        current_len += add_len
    return " ".join(res_list)
