# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt 模板加载（.md 文件，类级缓存，对齐 deepsearch SimpleReactSearchAgent 惯例）。"""

from pathlib import Path

_CACHE: dict[str, str] = {}
_PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """按文件名（不含扩展名）加载 prompt 模板文本。"""
    if name not in _CACHE:
        _CACHE[name] = (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    return _CACHE[name]
