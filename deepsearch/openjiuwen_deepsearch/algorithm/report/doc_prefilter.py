# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""子报告候选文档预筛工具。

本模块负责在 LLM 分类前统一处理 doc_info 去重。
URL 规范化和正文 hash 只用于候选归并，不会替换下游展示或引用使用的原始 URL。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    build_content_dedup_hash,
    canonicalize_url,
)


SHORT_PATH_KEEP_QUERY_MAX_LEN = 1


def normalize_url_for_dedup(url: str) -> str:
    """把 URL 规范化为去重 key。

    Args:
        url: 原始 URL，可以为空或非标准 URL。

    Returns:
        规范化后的 URL key；无法解析完整 URL 时返回公共规范化后的原值。
    """

    canonical_url = canonicalize_url(str(url or ""))
    if not canonical_url:
        return ""

    parts = urlsplit(canonical_url)
    if not parts.scheme or not parts.netloc:
        return canonical_url

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.startswith("m."):
        netloc = netloc[2:]

    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    for suffix in ("/index.html", "/index.htm", "/index"):
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)] or "/"
            break

    path_segments = [segment for segment in path.split("/") if segment]
    query = parts.query if len(path_segments) <= SHORT_PATH_KEEP_QUERY_MAX_LEN else ""

    return urlunsplit((scheme, netloc, path, query, ""))


def build_normalized_content_key(doc_info: dict[str, Any]) -> str:
    """用 collector 侧同一套正文规范化规则生成内容 hash。

    Args:
        doc_info: 候选文档信息，优先读取 core_content，其次读取 original_content。

    Returns:
        归一化正文的 SHA256 hash。正文为空时返回空字符串对应的 hash。
    """

    content = doc_info.get("core_content") or doc_info.get("original_content") or ""
    return build_content_dedup_hash(content)


def get_doc_source_id(doc_info: dict[str, Any]) -> str:
    """统一读取 doc_info 中的 source_id。

    Args:
        doc_info: 候选文档信息，兼容顶层 source_id 和 content_ref.source_id。

    Returns:
        source_id 字符串；不存在时返回空字符串。
    """

    source_id = doc_info.get("source_id")
    if source_id not in (None, ""):
        return str(source_id)

    content_ref = doc_info.get("content_ref")
    if isinstance(content_ref, dict):
        source_id = content_ref.get("source_id")
        if source_id not in (None, ""):
            return str(source_id)
    return ""


def build_doc_variant_key(doc_info: dict[str, Any]) -> str:
    """构造同一 URL 下的正文变体 key。

    Args:
        doc_info: 候选文档信息。

    Returns:
        带类型前缀的正文变体 key，例如 source_id:xxx 或 content:hash。
    """

    source_id = get_doc_source_id(doc_info)
    if source_id:
        return f"source_id:{source_id}"
    return f"content:{build_normalized_content_key(doc_info)}"


def _content_length(doc_info: dict[str, Any]) -> int:
    return len(str(doc_info.get("core_content") or doc_info.get("original_content") or ""))


def _representative_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
    original_index, doc_info = item
    return (_content_length(doc_info), -original_index)


def deduplicate_doc_infos(doc_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """统一 doc_info 去重入口。

    同 URL 且同正文变体只保留首条代表；同 URL 但不同 source/content 结果全部保留。

    Args:
        doc_infos: 待去重的候选文档列表，非 dict 条目会被跳过。

    Returns:
        去重后的浅拷贝文档列表，顺序按首次出现的去重 key 保持稳定。
    """

    grouped: dict[tuple[Any, ...], tuple[int, dict[str, Any]]] = {}
    order: list[tuple[Any, ...]] = []
    for index, raw_doc in enumerate(doc_infos or []):
        if not isinstance(raw_doc, dict):
            continue
        doc = raw_doc.copy()
        key = (
            normalize_url_for_dedup(doc.get("url", "")),
            build_doc_variant_key(doc),
        )

        current = grouped.get(key)
        if current is None:
            grouped[key] = (index, doc)
            order.append(key)
            continue
        if _representative_sort_key((index, doc)) > _representative_sort_key(current):
            grouped[key] = (index, doc)

    return [grouped[key][1] for key in order]
