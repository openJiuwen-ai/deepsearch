# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any


def normalize_key_passages(value: object) -> list[str]:
    """Normalize key_passages into a clean string list."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []

    passages = []
    for item in value:
        text = str(item or "").strip()
        if text:
            passages.append(text)
    return passages


def _format_key_passages(passages: list[str]) -> list[str]:
    if not passages:
        return ["[]"]
    return [f"- {passage}" for passage in passages]


def format_key_passage_block(doc_info: dict[str, Any], index: int) -> str:
    """Format one selected document as a key-passages-only block for outline."""
    passages = normalize_key_passages(doc_info.get("key_passages"))
    # Passage-level fallback: use passage_text if key_passages is empty
    if not passages:
        passage_text = doc_info.get("passage_text", "")
        if passage_text:
            passages = [passage_text]
    return "\n".join(
        [
            f"Document {index} key passages:",
            *_format_key_passages(passages),
        ]
    )

