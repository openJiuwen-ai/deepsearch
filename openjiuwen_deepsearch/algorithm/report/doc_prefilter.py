# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""子报告候选文档预筛工具。

本模块负责在 LLM 分类前统一处理 doc_info 去重、打分、按 step 分桶和均衡拆批。
URL 规范化和正文 hash 只用于候选归并，不会替换下游展示或引用使用的原始 URL。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    build_content_dedup_hash,
    canonicalize_url,
)


SCORE_KEYS = ("relevance", "answerability", "authority", "data_density")
DEFAULT_SCORE_WEIGHTS = {
    "relevance": 0.4,
    "answerability": 0.3,
    "authority": 0.15,
    "data_density": 0.15,
}
SHORT_PATH_KEEP_QUERY_MAX_LEN = 1


@dataclass(frozen=True)
class DocScore:
    """文档四维评分及综合分。

    字段值统一归一化到 0 到 1，方便跨来源排序和加权计算。
    """

    relevance: float = 0.0
    answerability: float = 0.0
    authority: float = 0.0
    data_density: float = 0.0
    composite: float = 0.0


@dataclass
class PrefilteredDoc:
    """预筛过程中的文档包装结构。

    保存原始文档、去重 key、正文变体 key、step 分桶 key、评分和输入顺序。
    """

    doc_info: dict[str, Any]
    dedup_key: str
    content_key: str
    step_key: str
    score: DocScore
    original_index: int


@dataclass
class PrefilterResult:
    """候选预筛结果及调试统计信息。

    doc_infos 是最终进入 LLM 分类的候选；deduped_doc_infos 是 fallback 使用的去重全集。
    其余字段用于日志、调试导出和端到端评估。
    """

    doc_infos: list[dict[str, Any]]
    deduped_doc_infos: list[dict[str, Any]]
    candidate_limit: int
    original_count: int = 0
    url_key_count: int = 0
    content_variant_count: int = 0
    filtered_count: int = 0
    step_bucket_count: int = 0
    step_bucket_quota: dict[str, int] = field(default_factory=dict)
    step_bucket_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    score_stats: dict[str, float] = field(default_factory=dict)


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


def _parse_score_value(value: Any) -> float:
    """按当前 10 分制解析分数，并归一化到 0 到 1。"""

    if value is None or value == "":
        return 0
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if not match:
            return 0
        value = match.group(0)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0

    return max(0, min(numeric / 10, 1))


def _score_source(doc_info: dict[str, Any]) -> dict[str, Any]:
    scores = doc_info.get("scores")
    if isinstance(scores, dict):
        return scores
    evaluation_scores = doc_info.get("evaluation_scores")
    if isinstance(evaluation_scores, dict):
        return evaluation_scores
    return {
        "authority": doc_info.get("source_authority"),
        "relevance": doc_info.get("task_relevance"),
        "answerability": doc_info.get("information_richness"),
        "data_density": doc_info.get("data_density"),
    }


def extract_doc_score(
    doc_info: dict[str, Any],
    score_weights: dict[str, float] | None = None,
) -> DocScore:
    """提取文档评分并计算综合分。

    Args:
        doc_info: 候选文档信息，优先读取 scores，兼容 evaluation_scores 和旧字段。
        score_weights: 四维评分权重；为空时使用默认权重。

    Returns:
        归一化后的四维评分和综合分。缺失或异常分数按 0 处理。
    """

    weights = score_weights or DEFAULT_SCORE_WEIGHTS
    source = _score_source(doc_info)
    scores = {key: _parse_score_value(source.get(key)) for key in SCORE_KEYS}
    composite = sum(scores[key] * weights.get(key, 0) for key in SCORE_KEYS)
    return DocScore(
        relevance=scores["relevance"],
        answerability=scores["answerability"],
        authority=scores["authority"],
        data_density=scores["data_density"],
        composite=composite,
    )


def _representative_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int, int]:
    original_index, doc_info = item
    return (extract_doc_score(doc_info).composite, _content_length(doc_info), -original_index)


def deduplicate_doc_infos(doc_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """统一 doc_info 去重入口。

    同 URL 且同正文变体只保留高分代表；同 URL 但不同 source/content 结果全部保留。

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


def get_step_bucket_key(doc_info: dict[str, Any]) -> str:
    """生成预筛 step 分桶 key。

    Args:
        doc_info: 候选文档信息，通常由收集阶段补充 step metadata。

    Returns:
        plan_idx+step_idx、step_task 或 unknown_step 构成的分桶 key。
    """

    plan_idx = doc_info.get("plan_idx")
    step_idx = doc_info.get("step_idx")
    if plan_idx not in (None, "") and step_idx not in (None, ""):
        return f"plan:{plan_idx}:step:{step_idx}"

    step_task = doc_info.get("step_task")
    if step_task:
        return f"step_task:{step_task}"

    return "unknown_step"


def _allocate_step_quotas(
    buckets: dict[str, list[PrefilteredDoc]],
    candidate_limit: int,
) -> dict[str, int]:
    """给 step 分桶分配候选名额，尽量保证每个非空 step 有基础覆盖。"""

    if candidate_limit <= 0 or not buckets:
        return {}
    if len(buckets) > candidate_limit:
        return {}

    quotas = {step_key: 1 for step_key in buckets}
    remaining = candidate_limit - len(buckets)
    if remaining <= 0:
        return quotas

    weights = {step_key: max(len(items) - 1, 0) for step_key, items in buckets.items()}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return quotas

    shares: list[tuple[float, str]] = []
    assigned = 0
    for step_key, weight in weights.items():
        exact_share = remaining * weight / total_weight
        extra = int(exact_share)
        quotas[step_key] += extra
        assigned += extra
        shares.append((exact_share - extra, step_key))

    for _, step_key in sorted(shares, reverse=True)[: remaining - assigned]:
        quotas[step_key] += 1
    return quotas


def _doc_sort_key(doc: PrefilteredDoc) -> tuple[float, int, int]:
    return (doc.score.composite, _content_length(doc.doc_info), -doc.original_index)


def _score_stats(docs: list[PrefilteredDoc]) -> dict[str, float]:
    if not docs:
        return {}
    composites = [doc.score.composite for doc in docs]
    return {
        "min": min(composites),
        "max": max(composites),
        "avg": sum(composites) / len(composites),
    }


def prefilter_doc_infos_for_classification(
    doc_infos: list[dict[str, Any]],
    *,
    result_top_k: int = 10,
    prefilter_multiplier: int = 5,
    score_weights: dict[str, float] | None = None,
) -> PrefilterResult:
    """子报告 LLM 分类前的确定性候选预筛。

    Args:
        doc_infos: 原始候选文档列表。
        result_top_k: LLM 最终应收敛到的 URL 数量。
        prefilter_multiplier: 进入 LLM 的候选倍数上限，实际上限为 result_top_k * prefilter_multiplier。
        score_weights: 可选评分权重。

    Returns:
        预筛候选、去重全集和统计信息。
    """

    deduped_doc_infos = deduplicate_doc_infos(doc_infos)
    raw_limit = max(0, int(result_top_k or 0)) * max(0, int(prefilter_multiplier or 0))
    candidate_limit = min(len(deduped_doc_infos), raw_limit)

    wrapped_docs = [
        PrefilteredDoc(
            doc_info=doc,
            dedup_key=normalize_url_for_dedup(doc.get("url", "")),
            content_key=build_doc_variant_key(doc),
            step_key=get_step_bucket_key(doc),
            score=extract_doc_score(doc, score_weights),
            original_index=index,
        )
        for index, doc in enumerate(deduped_doc_infos)
    ]

    if candidate_limit <= 0:
        return PrefilterResult(
            doc_infos=[],
            deduped_doc_infos=deduped_doc_infos,
            candidate_limit=candidate_limit,
            original_count=len(doc_infos or []),
            url_key_count=len({doc.dedup_key for doc in wrapped_docs}),
            content_variant_count=len(wrapped_docs),
            filtered_count=0,
            score_stats=_score_stats(wrapped_docs),
        )

    buckets: dict[str, list[PrefilteredDoc]] = {}
    for doc in wrapped_docs:
        buckets.setdefault(doc.step_key, []).append(doc)
    for items in buckets.values():
        items.sort(key=_doc_sort_key, reverse=True)

    if len(buckets) > candidate_limit:
        # step 数超过候选上限时，无法做到每个 step 都保留，只能让各 step 第一名全局竞争。
        best_per_step = [items[0] for items in buckets.values()]
        selected = sorted(best_per_step, key=_doc_sort_key, reverse=True)[:candidate_limit]
        quotas = {doc.step_key: 1 for doc in selected}
    else:
        quotas = _allocate_step_quotas(buckets, candidate_limit)
        selected = []
        selected_ids = set()
        for step_key, quota in quotas.items():
            for doc in buckets.get(step_key, [])[:quota]:
                selected.append(doc)
                selected_ids.add(doc.original_index)
        if len(selected) < candidate_limit:
            remaining_docs = [
                doc for doc in sorted(wrapped_docs, key=_doc_sort_key, reverse=True)
                if doc.original_index not in selected_ids
            ]
            selected.extend(remaining_docs[: candidate_limit - len(selected)])

    selected.sort(key=lambda doc: doc.original_index)
    step_bucket_stats = {
        step_key: {"doc_count": len(items), "quota": quotas.get(step_key, 0)}
        for step_key, items in buckets.items()
    }

    return PrefilterResult(
        doc_infos=[doc.doc_info for doc in selected],
        deduped_doc_infos=deduped_doc_infos,
        candidate_limit=candidate_limit,
        original_count=len(doc_infos or []),
        url_key_count=len({doc.dedup_key for doc in wrapped_docs}),
        content_variant_count=len(wrapped_docs),
        filtered_count=len(selected),
        step_bucket_count=len(buckets),
        step_bucket_quota=quotas,
        step_bucket_stats=step_bucket_stats,
        score_stats=_score_stats(wrapped_docs),
    )


def build_balanced_doc_batches(doc_infos: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    """把候选均衡拆批，避免出现一个满批加一个很小尾批。

    Args:
        doc_infos: 待拆分的候选文档列表。
        batch_size: 单批最大候选数；小于 1 时按 1 处理。

    Returns:
        均衡后的批次列表。
    """

    if not doc_infos:
        return []
    safe_batch_size = max(1, int(batch_size or 1))
    batch_count = math.ceil(len(doc_infos) / safe_batch_size)
    base_size = len(doc_infos) // batch_count
    remainder = len(doc_infos) % batch_count

    batches = []
    start = 0
    for idx in range(batch_count):
        size = base_size + (1 if idx < remainder else 0)
        batches.append(doc_infos[start:start + size])
        start += size
    return batches
