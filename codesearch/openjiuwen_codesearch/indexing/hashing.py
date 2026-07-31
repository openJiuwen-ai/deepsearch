# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""确定性标识：文件哈希与 chunk ID。公式与旧实现一致（跨运行/跨 commit 去重的根基）。"""

import hashlib


def file_content_hash(rel_path: str, content: bytes) -> str:
    """sha256(相对路径 + 文件内容)。路径参与哈希：同内容不同路径视为不同文件。"""
    return hashlib.sha256(rel_path.encode("utf-8") + content).hexdigest()


def deterministic_chunk_id(file_hash: str, start_line: int, end_line: int, name: str) -> int:
    """INT64 chunk 主键：md5 前 15 个 hex 位（60 bit），同输入恒同值。"""
    key = f"{file_hash}_{start_line}_{end_line}_{name}"
    return int(hashlib.md5(key.encode()).hexdigest()[:15], 16)
