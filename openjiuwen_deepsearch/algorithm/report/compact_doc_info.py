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


def build_classify_scores(doc_info: dict[str, Any]) -> dict[str, Any]:
    """Build compact scores from structured scores."""
    scores = doc_info.get("scores")
    if isinstance(scores, dict) and scores:
        return dict(scores)

    return {}


def format_scores_inline(doc_info: dict[str, Any]) -> str:
    """Format structured scores as a stable inline text block."""
    scores = build_classify_scores(doc_info)
    if not scores:
        return "[]"
    return ", ".join(f"{key}: {value}" for key, value in scores.items())


def get_numeric_score(doc_info: dict[str, Any], score_name: str) -> float | None:
    """Read a numeric score from structured scores."""
    scores = doc_info.get("scores")
    if not isinstance(scores, dict):
        return None
    value = scores.get(score_name)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_scores(scores: dict[str, Any]) -> list[str]:
    if not scores:
        return ["[]"]
    return [f"  {key}: {value}" for key, value in scores.items()]


def _format_key_passages(passages: list[str]) -> list[str]:
    if not passages:
        return ["[]"]
    return [f"- {passage}" for passage in passages]


def build_compact_classify_doc_infos_text(doc_infos: list[dict[str, Any]]) -> str:
    """Build compact document information for classification LLM input."""
    blocks = []
    for index, doc_info in enumerate(doc_infos, start=1):
        scores = build_classify_scores(doc_info)
        passages = normalize_key_passages(doc_info.get("key_passages"))
        block_lines = [
            f"Document {index}:",
            f"url: {doc_info.get('url', '')}",
            f"title: {doc_info.get('title', '')}",
            f"doc_time: {doc_info.get('doc_time', '')}",
            f"publish_time: {doc_info.get('publish_time', '')}",
            "scores:",
            *_format_scores(scores),
            "key passages:",
            *_format_key_passages(passages),
        ]
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


def format_key_passage_block(doc_info: dict[str, Any], index: int) -> str:
    """Format one selected document as a key-passages-only block for outline."""
    passages = normalize_key_passages(doc_info.get("key_passages"))
    return "\n".join(
        [
            f"Document {index} key passages:",
            *_format_key_passages(passages),
        ]
    )


def build_key_passage_text(doc_infos: list[dict[str, Any]]) -> str:
    """Build a combined key passages text from selected document infos."""
    return "\n\n".join(
        format_key_passage_block(doc_info, index)
        for index, doc_info in enumerate(doc_infos, start=1)
    )
