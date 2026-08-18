# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""网页正文增强的纯研究逻辑。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    build_content_ref,
    canonicalize_url,
    extract_key_passages,
    generate_source_id,
)
from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH
from openjiuwen_deepsearch.utils.common_utils.date_utils import DocDate, merge_doc_dates, parse_date_string

#: 抓取正文默认超时秒数。
DEFAULT_FETCH_TIMEOUT_SECONDS = 45
#: 抓取正文最低字符数门槛。
MIN_FETCHED_CONTENT_LENGTH = 200

_NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)*(?:\s*(?:%|fps|hz|khz|mhz|bpm|mmhg|ms|min|minutes?|videos?|subjects?))?",
    re.IGNORECASE,
)
_TECHNICAL_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[A-Z]{2,}[A-Za-z0-9_-]*|[A-Za-z]+\d[A-Za-z0-9_-]*|[A-Za-z0-9]+-[A-Za-z0-9_-]+)\b"
)


class WebPageEnrichmentDecision(BaseModel):
    """网页正文增强 URL 选择结果。

    Attributes:
        selected_indexes: 候选列表中需要抓取正文的 candidate_index 列表。
    """

    selected_indexes: list[int] = Field(default_factory=list, description="需要抓取正文的 candidate_index 列表")


class WebPageEvidenceContent(BaseModel):
    """网页正文压缩后的证据内容。

    Attributes:
        original_content: 面向当前研究步骤整理后的证据正文。
        key_passages: 从整理后正文中提取的关键片段。
    """

    original_content: str = Field(default="", description="整理后的证据正文")
    key_passages: list[str] = Field(default_factory=list, description="整理后的关键片段")


def _is_http_url(url: str) -> bool:
    """判断 URL 是否为 HTTP/HTTPS 网页。

    Args:
        url: 待判断的 URL。

    Returns:
        是 HTTP 或 HTTPS URL 时返回 True。
    """
    try:
        return urlparse(str(url or "").strip()).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


def has_sufficient_fetched_content(
    fetched: dict | None,
    minimum_content_length: int = MIN_FETCHED_CONTENT_LENGTH,
) -> bool:
    """判断抓取结果是否包含足够正文供增强使用。

    Args:
        fetched: 网页抓取结果。
        minimum_content_length: 当前文档要求的最低正文长度，调用方应保证不小于
            ``MIN_FETCHED_CONTENT_LENGTH``。

    Returns:
        正文去除首尾空白后达到 ``minimum_content_length`` 时返回 True。
    """
    if not isinstance(fetched, dict):
        return False
    return len(str(fetched.get("content") or "").strip()) >= int(minimum_content_length)


def is_explicit_pdf_url(url: str) -> bool:
    """判断 URL 路径是否显式指向 PDF 文件。

    Args:
        url: 待检查的网页 URL。

    Returns:
        URL 路径以 `.pdf` 结尾时返回 True。
    """
    try:
        return urlparse(str(url or "")).path.casefold().endswith(".pdf")
    except ValueError:
        return False


def has_pdf_magic(fetched: dict | None) -> bool:
    """判断抓取正文是否仍是未解析的 PDF 原始数据。

    Args:
        fetched: 网页抓取结果。

    Returns:
        正文去除前导空白后以 PDF 文件魔数开头时返回 True。
    """
    if not isinstance(fetched, dict):
        return False
    content = str(fetched.get("content") or "").lstrip("\ufeff\t\r\n ")
    return content.startswith("%PDF-")


def coerce_fetch_timeout_seconds(value: Any, default: int = DEFAULT_FETCH_TIMEOUT_SECONDS) -> int:
    """把运行态抓取超时配置转换为可用秒数。

    Args:
        value: 运行态配置值。
        default: 转换失败或非正数时使用的默认秒数。

    Returns:
        可用于网页抓取的正整数超时时间。
    """
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return default
    if timeout <= 0:
        return default
    return timeout


def _score_sort_value(doc_info: dict[str, Any]) -> float:
    """计算候选排序分数。

    Args:
        doc_info: 搜索结果文档信息。

    Returns:
        基于 relevance、answerability、data_density 的轻量排序分数。
    """
    scores = doc_info.get("scores") if isinstance(doc_info.get("scores"), dict) else {}
    total = 0.0
    for key in ("relevance", "answerability", "data_density"):
        try:
            total += float(scores.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _build_candidate(index: int, doc_info: dict[str, Any], url: str) -> dict[str, Any]:
    """构造单个内部候选。

    Args:
        index: 文档在本轮新增列表中的索引。
        doc_info: 搜索结果文档信息。
        url: 已清理首尾空白的原始 URL。

    Returns:
        包含内部 doc_index 和排序分数的候选字典。
    """
    return {
        "doc_index": index,
        "url": url,
        "title": doc_info.get("title", ""),
        "source": doc_info.get("source", ""),
        "query": doc_info.get("query", ""),
        "key_passages": doc_info.get("key_passages", []),
        "scores": doc_info.get("scores", {}),
        "_sort_score": _score_sort_value(doc_info),
    }


def _rank_and_number_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """排序、截断并补充候选索引。

    Args:
        candidates: 带内部排序分数的候选列表。
        limit: 最多保留的候选数量。

    Returns:
        已移除内部排序分数并补充 candidate_index 的候选列表。
    """
    candidates.sort(key=lambda item: item.get("_sort_score", 0), reverse=True)
    trimmed = candidates[:max(0, int(limit))]
    for candidate_index, item in enumerate(trimmed):
        item["candidate_index"] = candidate_index
        item.pop("_sort_score", None)
    return trimmed


def build_enrichment_candidates(doc_infos: list[dict], limit: int = 10) -> list[dict]:
    """构造网页增强选择 LLM 的候选列表。

    Args:
        doc_infos: 本轮新增 doc_infos。
        limit: 最多返回候选数量。

    Returns:
        不含 original_content 的候选列表，字段足够支持 LLM 做选择。
    """
    seen_urls: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for index, doc_info in enumerate(doc_infos or []):
        if not isinstance(doc_info, dict):
            continue
        if doc_info.get("skip_webpage_enrichment") is True:
            continue
        url = str(doc_info.get("url") or "").strip()
        canonical_url = canonicalize_url(url)
        if not _is_http_url(url) or not canonical_url or canonical_url in seen_urls:
            continue
        if isinstance(doc_info.get("enrichment"), dict) and doc_info["enrichment"].get("webpage_fetched") is True:
            continue
        seen_urls.add(canonical_url)
        candidates.append(_build_candidate(index, doc_info, url))
    return _rank_and_number_candidates(candidates, limit)


def build_selection_prompt_candidates(candidates: list[dict]) -> list[dict]:
    """构造给网页增强选择 LLM 的候选列表。

    Args:
        candidates: 内部候选列表，包含 `candidate_index` 和 `doc_index`。

    Returns:
        移除内部 `doc_index` 后的候选列表。
    """
    visible_keys = ("candidate_index", "url", "title", "source", "query", "key_passages", "scores")
    return [
        {key: candidate.get(key, "") for key in visible_keys if key in candidate}
        for candidate in candidates or []
        if isinstance(candidate, dict)
    ]


def _build_task_payload(state: dict[str, Any]) -> dict[str, Any]:
    """构造网页增强 Prompt 共用的任务上下文。

    Args:
        state: 网页增强节点运行状态。

    Returns:
        只包含任务描述的 JSON 可序列化字典。
    """
    return {
        "task": {
            "plan_title": state.get("plan_title", ""),
            "plan_thought": state.get("plan_thought", ""),
            "step_title": state.get("step_title", ""),
            "step_description": state.get("step_description", ""),
        },
    }


def build_selection_user_payload(state: dict[str, Any], candidates: list[dict]) -> dict[str, Any]:
    """构造候选选择 LLM 的不可信 user payload。

    Args:
        state: 网页增强节点运行状态。
        candidates: 内部候选列表。

    Returns:
        包含任务、数量上限和可见候选字段的 JSON 数据。
    """
    payload = _build_task_payload(state)
    payload.update({
        "max_urls": state.get("max_urls", 3),
        "candidates": build_selection_prompt_candidates(candidates),
    })
    return payload


def build_compression_user_payload(
    state: dict[str, Any],
    doc_info: dict[str, Any],
    fetched: dict[str, Any],
) -> dict[str, Any]:
    """构造正文压缩 LLM 的不可信 user payload。

    Args:
        state: 网页增强节点运行状态。
        doc_info: 当前候选对应的搜索文档。
        fetched: 网页抓取结果。

    Returns:
        包含任务、旧证据、抓取正文和输出上限的 JSON 数据。
    """
    payload = _build_task_payload(state)
    payload.update({
        "document": {
            "query": doc_info.get("query", ""),
            "title": doc_info.get("title", ""),
            "url": doc_info.get("url", ""),
            "key_passages": doc_info.get("key_passages", []),
            "scores": doc_info.get("scores", {}),
        },
        "existing_content": str(doc_info.get("original_content") or "")[:MAX_COLLECTOR_DOC_CONTENT_LENGTH],
        "webpage_content": truncate_raw_content_for_compression(str(fetched.get("content") or "")),
        "max_content_length": MAX_COLLECTOR_DOC_CONTENT_LENGTH,
    })
    return payload


def sanitize_selected_indexes(selected_indexes: list[int], candidate_count: int, max_urls: int) -> list[int]:
    """清洗 LLM 返回的候选索引。

    Args:
        selected_indexes: LLM 返回的 candidate_index 列表。
        candidate_count: 当前候选数量。
        max_urls: 最多允许选择的 URL 数。

    Returns:
        去重、过滤越界并截断后的索引列表。
    """
    output: list[int] = []
    seen: set[int] = set()
    for value in selected_indexes or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= candidate_count or index in seen:
            continue
        output.append(index)
        seen.add(index)
        if len(output) >= max(0, int(max_urls)):
            break
    return output


def truncate_raw_content_for_compression(content: str) -> str:
    """截断进入网页正文压缩 LLM 的原始网页正文。

    Args:
        content: 抓取到的原始网页正文。

    Returns:
        最多 `MAX_COLLECTOR_DOC_CONTENT_LENGTH * 10` 字符的正文。
    """
    return str(content or "")[:MAX_COLLECTOR_DOC_CONTENT_LENGTH * 10]


def _normalize_quality_text(value: Any) -> str:
    """规范化质量门禁使用的文本。

    Args:
        value: 待规范化值。

    Returns:
        经过大小写和连续空白归一化的文本。
    """
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _normalize_fact_text(value: Any) -> str:
    """规范化用于事实锚点匹配的文本。

    Args:
        value: 待规范化值。

    Returns:
        移除空格和标点并统一大小写后的文本。
    """
    return re.sub(r"[\W_]+", "", str(value or "").casefold())


def _extract_fact_anchors(text: str) -> set[str]:
    """提取应在增强后继续保留的数字和技术标识。

    Args:
        text: 旧证据关键片段。

    Returns:
        规范化后的数字、单位值和技术标识集合。
    """
    anchors = {
        _normalize_fact_text(match.group(0))
        for pattern in (_NUMERIC_FACT_PATTERN, _TECHNICAL_IDENTIFIER_PATTERN)
        for match in pattern.finditer(text or "")
    }
    return {anchor for anchor in anchors if anchor}


def should_replace_original_content(
    original_doc: dict,
    evidence: WebPageEvidenceContent,
) -> tuple[bool, str]:
    """判断增强结果是否足以替换旧正文。

    质量门禁要求旧关键片段中的数字和技术标识继续出现在新证据中。
    旧文为空时直接接受有效新正文，避免阻碍原本没有摘要的搜索结果增强。

    Args:
        original_doc: 增强前的 doc_info。
        evidence: 压缩 LLM 生成的新证据。

    Returns:
        `(是否替换, 原因)`；拒绝时原因可用于日志定位。
    """
    original_content = str(original_doc.get("original_content") or "").strip()
    enriched_content = str(evidence.original_content or "").strip()
    if not enriched_content:
        return False, "empty_enriched_content"
    if not original_content:
        return True, "no_existing_content"

    normalized_enriched = _normalize_quality_text(enriched_content)
    if _normalize_quality_text(original_content) in normalized_enriched:
        return True, "existing_content_preserved"
    normalized_fact_enriched = _normalize_fact_text(enriched_content)

    passages = [
        str(passage or "").strip()
        for passage in (original_doc.get("key_passages") or [])
        if str(passage or "").strip()
    ]
    if not passages:
        passages = extract_key_passages(
            content=original_content,
            query=str(original_doc.get("query") or ""),
            title=str(original_doc.get("title") or ""),
        )

    missing_anchors: set[str] = set()
    for passage in passages:
        for anchor in _extract_fact_anchors(passage):
            if anchor not in normalized_fact_enriched:
                missing_anchors.add(anchor)

    if missing_anchors:
        return False, f"missing_fact_anchors:{','.join(sorted(missing_anchors))}"
    return True, "quality_guard_passed"


def capture_doc_identity(doc_info: dict) -> dict[str, str]:
    """捕获增强前用于同步列表的定位键。

    Args:
        doc_info: 待增强的 doc_info。

    Returns:
        包含 source_id、doc_id、url、query 的定位字典。
    """
    return {
        "source_id": str(doc_info.get("source_id") or ""),
        "doc_id": str(doc_info.get("doc_id") or ""),
        "url": str(doc_info.get("url") or ""),
        "query": str(doc_info.get("query") or ""),
    }


def _find_by_secondary_identity(
    doc_infos: list[dict],
    identity: dict[str, str],
    *,
    require_missing_source_id: bool,
) -> int | None:
    """使用带 query 约束的次级身份查找文档。

    Args:
        doc_infos: 待查找的文档列表。
        identity: 增强前文档身份。
        require_missing_source_id: 是否只允许匹配缺少 source_id 的旧数据。

    Returns:
        找到时返回列表索引，否则返回 None。
    """
    doc_id = identity.get("doc_id", "")
    url = identity.get("url", "")
    query = identity.get("query", "")

    def is_eligible(doc: Any) -> bool:
        """判断候选文档是否允许参与次级身份匹配。

        Args:
            doc: 待判断的候选文档。

        Returns:
            候选为字典且满足 source_id 兼容约束时返回 True。
        """
        return isinstance(doc, dict) and not (
            require_missing_source_id and doc.get("source_id")
        )

    if query:
        if doc_id:
            for index, doc in enumerate(doc_infos or []):
                if is_eligible(doc) and doc.get("doc_id") == doc_id and doc.get("query") == query:
                    return index
        if url:
            for index, doc in enumerate(doc_infos or []):
                if is_eligible(doc) and doc.get("url") == url and doc.get("query") == query:
                    return index
        return None

    if doc_id:
        for index, doc in enumerate(doc_infos or []):
            if is_eligible(doc) and doc.get("doc_id") == doc_id:
                return index
    if url:
        for index, doc in enumerate(doc_infos or []):
            if is_eligible(doc) and doc.get("url") == url:
                return index
    return None


def find_matching_doc_index(doc_infos: list[dict], identity: dict[str, str]) -> int | None:
    """在 doc_infos 中查找增强前身份对应的条目。

    Args:
        doc_infos: 待同步的文档列表。
        identity: `capture_doc_identity` 返回的定位键。

    Returns:
        找到时返回列表索引，否则返回 None。
    """
    source_id = identity.get("source_id", "")
    if source_id:
        for index, doc in enumerate(doc_infos or []):
            if isinstance(doc, dict) and doc.get("source_id") == source_id:
                return index
        # 非空 source_id 未命中通常表示不同证据版本；仅允许回退匹配缺少
        # source_id 的旧数据，并使用 query 约束避免跨检索任务覆盖。
        return _find_by_secondary_identity(
            doc_infos,
            identity,
            require_missing_source_id=True,
        )
    return _find_by_secondary_identity(
        doc_infos,
        identity,
        require_missing_source_id=False,
    )


def apply_enrichment_to_doc(
    doc_info: dict[str, Any],
    evidence: WebPageEvidenceContent,
    fetched: dict[str, Any],
) -> dict[str, Any]:
    """把压缩后的网页证据写回 doc_info。

    Args:
        doc_info: 原始 doc_info。
        evidence: 压缩后的证据正文和关键片段。
        fetched: 抓取结果。

    Returns:
        更新后的 doc_info 副本。
    """
    updated = dict(doc_info)
    doc_id = str(updated.get("doc_id") or "")
    source_id = generate_source_id(doc_id, content=evidence.original_content)
    updated["source_id"] = source_id
    updated["original_content"] = evidence.original_content
    updated["key_passages"] = evidence.key_passages
    updated["content_ref"] = build_content_ref(doc_id=doc_id, stored=True, source_id=source_id)
    enrichment = dict(updated.get("enrichment") or {})
    enrichment.update({
        "webpage_fetched": True,
        "fetch_status_code": fetched.get("status_code", ""),
        "fetched_url": fetched.get("url", ""),
        "content_source": fetched.get("fetch_method", "harness_webpage_fetch"),
    })
    updated["enrichment"] = enrichment
    return updated


#: publish_time 的未知占位符，与 collector_evidence 归一化阶段保持一致。
UNKNOWN_PUBLISH_TIME = "未提供时间信息"


def _doc_date_from_dict(value: Any) -> DocDate | None:
    """把 DocDate.to_dict() 格式的字典还原为 DocDate。

    Args:
        value: 待解析的 date_info/doc_date 字典。

    Returns:
        合法的 DocDate；字段缺失或非法时返回 None。
    """
    if not isinstance(value, dict):
        return None
    day = parse_date_string(value.get("date"))
    granularity = value.get("granularity")
    confidence = value.get("confidence")
    if (
        day is None
        or granularity not in ("year", "month", "day")
        or confidence not in ("high", "medium", "low")
    ):
        return None
    return DocDate(
        day=day,
        granularity=granularity,
        confidence=confidence,
        source=str(value.get("source") or ""),
    )


def _doc_date_from_date_metadata(value: Any) -> DocDate | None:
    """把归一化阶段的引擎 date_metadata 转换为 high 置信 DocDate。

    Args:
        value: doc_info 上的 date_metadata 字典。

    Returns:
        引擎 published 日期对应的 DocDate；缺失或非法时返回 None。
    """
    if not isinstance(value, dict):
        return None
    if str(value.get("type") or "") != "published":
        return None
    day = parse_date_string(value.get("parsed_date"))
    if day is None:
        return None
    field = str(value.get("field") or "source_date")
    return DocDate(day=day, granularity="day", confidence="high", source=f"engine:{field}")


def merge_fetched_doc_date(doc_info: dict[str, Any], fetched: dict[str, Any]) -> dict[str, Any]:
    """把富化抓取顺带的 HTML head 日期合并进 doc_info 的 date_info。

    与 doc 已有日期(已有 date_info、引擎 date_metadata)按 merge_doc_dates
    规则合并：取最高置信档，同档矛盾降级为 unknown(移除 date_info)。
    抓取无日期时文档保持不变；publish_time 仍为未知占位符且合并出日期时，
    同步更新 publish_time 用于展示。

    Args:
        doc_info: 待写回的 doc_info。
        fetched: 网页抓取结果，可能携带 ``doc_date``(DocDate.to_dict() 格式)。

    Returns:
        更新后的 doc_info 副本。
    """
    fetched_date = _doc_date_from_dict(fetched.get("doc_date") if isinstance(fetched, dict) else None)
    if fetched_date is None:
        return doc_info
    updated = dict(doc_info)
    merged = merge_doc_dates([
        fetched_date,
        _doc_date_from_dict(updated.get("date_info")),
        _doc_date_from_date_metadata(updated.get("date_metadata")),
    ])
    if merged is None:
        updated.pop("date_info", None)
        return updated
    updated["date_info"] = merged.to_dict()
    publish_time = str(updated.get("publish_time") or "").strip()
    if not publish_time or publish_time == UNKNOWN_PUBLISH_TIME:
        updated["publish_time"] = merged.day.isoformat()
    return updated


def apply_document_replacements(
    doc_infos: list[dict],
    replacements: list[tuple[dict[str, str], dict[str, Any]]],
) -> list[dict]:
    """按增强前身份替换文档列表中的证据。

    Args:
        doc_infos: 待同步的文档列表。
        replacements: `(增强前身份, 增强后文档)` 列表。

    Returns:
        应用替换后的新文档列表，未匹配条目保持原值。
    """
    updated_docs = list(doc_infos or [])
    for identity, enriched_doc in replacements or []:
        index = find_matching_doc_index(updated_docs, identity)
        if index is not None:
            updated_docs[index] = dict(enriched_doc)
    return updated_docs


def synchronize_history_queries(
    history_queries: list,
    replacements: list[tuple[dict[str, str], dict[str, Any]]],
) -> list:
    """把增强文档同步到历史检索 query。

    Args:
        history_queries: collector 历史 query，支持 Pydantic 模型和字典形态。
        replacements: `(增强前身份, 增强后文档)` 列表。

    Returns:
        保持原 query 形态的同步结果列表。

    Raises:
        TypeError: history query 既不是字典，也不支持 Pydantic `model_copy` 时抛出。
    """
    synchronized = []
    for query in history_queries or []:
        if isinstance(query, dict):
            updated_query = dict(query)
            updated_query["doc_infos"] = apply_document_replacements(
                query.get("doc_infos") or [],
                replacements,
            )
        else:
            model_copy = getattr(query, "model_copy", None)
            if not callable(model_copy):
                raise TypeError("history query must be a dict or support model_copy")
            updated_query = model_copy(update={
                "doc_infos": apply_document_replacements(
                    getattr(query, "doc_infos", None) or [],
                    replacements,
                )
            })
        synchronized.append(updated_query)
    return synchronized
