# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH

logger = logging.getLogger(__name__)

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"spm", "from", "source", "ref", "fbclid", "gclid"}
MAX_PASSAGE_LENGTH = 500
DEFAULT_KEY_PASSAGE_COUNT = 5
SCORE_KEYS = ("authority", "relevance", "answerability", "data_density")


@dataclass
class CollectorSourceStore:
    """保存 collector 子图内可回查的原始正文。

    Attributes:
        contents: source_id 到正文片段的映射；Phase 1 仅作为 session 内临时存储。
    """

    contents: dict[str, str] = field(default_factory=dict)

    def write(self, source_id: str, content: str) -> bool:
        """写入原始正文。

        Args:
            source_id: 证据片段稳定 ID。
            content: 原始正文。

        Returns:
            写入或已存在可回查内容时返回 True；输入无效时返回 False。
        """
        if not source_id:
            return False
        normalized_content = content or ""
        if source_id in self.contents:
            existing_key = normalize_content_for_dedup(self.contents[source_id])
            incoming_key = normalize_content_for_dedup(normalized_content)
            if existing_key != incoming_key:
                logger.warning(
                    "[CollectorEvidence] source_store source_id conflict. source_id=%s | keeping first content.",
                    source_id,
                )
            return True
        self.contents[source_id] = normalized_content
        return True

    def read(self, source_id: str) -> str | None:
        """按 source_id 读取原始正文。

        Args:
            source_id: 证据片段稳定 ID。

        Returns:
            找到时返回正文；不存在时返回 None。
        """
        return self.contents.get(source_id)

    def to_dict(self) -> dict[str, str]:
        """导出可写入 session state 的字典。

        Returns:
            source_id 到正文的映射副本。
        """
        return dict(self.contents)

    @classmethod
    def from_dict(cls, value: dict | None) -> "CollectorSourceStore":
        """从 session state 字典恢复 source store。

        Args:
            value: session 中保存的 source store 字典。

        Returns:
            CollectorSourceStore 实例。
        """
        if not isinstance(value, dict):
            return cls()
        return cls(contents={str(key): str(content or "") for key, content in value.items()})


def _short_hash(value: str) -> str:
    """生成短 hash，避免把 URL 或本地文件 ID 暴露到 doc_id。

    Args:
        value: 待哈希的稳定身份字符串。

    Returns:
        16 位十六进制短 hash。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def normalize_content_for_dedup(content: Any) -> str:
    """正文去重前的统一规范化。

    Args:
        content: 原始正文，允许传入 None 或非字符串值。

    Returns:
        经过 NFKC、换行和连续空白归一化后的正文。
    """

    normalized = unicodedata.normalize("NFKC", str(content or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def build_content_dedup_hash(content: Any) -> str:
    """生成 collector/report 共用的正文去重 hash。

    Args:
        content: 原始正文。

    Returns:
        归一化正文的 SHA256 hash。
    """

    normalized = normalize_content_for_dedup(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonicalize_url(url: str) -> str:
    """归一化 URL，去掉常见跟踪参数。

    Args:
        url: 原始 URL。

    Returns:
        归一化后的 URL；无法解析时返回原值。
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    kept_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS or lower_key.startswith(TRACKING_QUERY_PREFIXES):
            continue
        kept_query.append((key, value))
    kept_query = sorted(kept_query)
    normalized_path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            normalized_path,
            urlencode(kept_query),
            "",
        )
    )


def generate_doc_id(url: str, title: str, source_type: str = "web") -> str:
    """生成原始文档稳定 ID。

    Args:
        url: 文档 URL 或 localdataset URL。
        title: 文档标题。
        source_type: 来源类型，常见值为 web 或 local。

    Returns:
        带来源前缀的稳定 doc_id。
    """
    prefix = "local" if source_type == "local" or str(url).startswith("localdataset://") else "web"
    identity = canonicalize_url(url) or f"{title}|{source_type}"
    return f"{prefix}_{_short_hash(identity)}"


def generate_source_id(
    doc_id: str,
    passage_index: int | None = None,
    content: str | None = None,
) -> str:
    """生成 evidence/citation 身份 ID。

    Args:
        doc_id: 原始文档稳定 ID。
        passage_index: 显式片段序号；提供时优先用于生成稳定 source_id。
        content: 证据片段正文；同一 doc_id 下不同 content 会生成不同 source_id。

    Returns:
        source_id。未提供片段信息时保持兼容，默认等于 doc_id。
    """
    if passage_index is not None:
        return f"{doc_id}_p{passage_index}"
    normalized_content = normalize_content_for_dedup(content)
    if normalized_content:
        return f"{doc_id}_p{_short_hash(normalized_content)}"
    return doc_id


def build_content_ref(doc_id: str, stored: bool, source_id: str | None = None) -> dict[str, str]:
    """构造正文引用。

    Args:
        doc_id: 原始文档稳定 ID。
        stored: 是否已写入 source store。
        source_id: source store 中可回查的证据片段 ID。

    Returns:
        content_ref 字典。写入失败时使用 legacy_doc_infos 降级类型。
    """
    ref_type = "source_store" if stored else "legacy_doc_infos"
    content_ref = {"type": ref_type, "doc_id": doc_id}
    if source_id:
        content_ref["source_id"] = source_id
    return content_ref


def read_content_by_ref(
    content_ref: dict[str, Any] | None,
    source_store: CollectorSourceStore,
    legacy_content: str = "",
) -> str:
    """按 content_ref 回查正文。

    Args:
        content_ref: 正文引用。
        source_store: 当前 collector source store。
        legacy_content: 兼容期 `doc_infos.original_content` 兜底正文。

    Returns:
        正文内容；回查失败时返回兼容正文或空字符串。
    """
    if not isinstance(content_ref, dict):
        return legacy_content or ""
    doc_id = str(content_ref.get("doc_id") or "")
    source_id = str(content_ref.get("source_id") or doc_id)
    if content_ref.get("type") == "source_store" and source_id:
        content = source_store.read(source_id)
        if content is not None:
            return content
        logger.warning(
            "[CollectorEvidence] content_ref missing in source_store. doc_id=%s | source_id=%s",
            doc_id,
            source_id,
        )
    return legacy_content or ""


def extract_source(url: str) -> str:
    """提取文档来源标识。

    Args:
        url: 文档 URL。

    Returns:
        Web 域名或 localdataset。
    """
    if str(url).startswith("localdataset://"):
        return "localdataset"
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def split_passages(content: str) -> list[str]:
    """把正文切分为适合评分的段落。

    中文句末标点可直接切分；英文句点仅在后接空白或文本结束时作为句末，
    避免把 `1.5%`、`3.10.2`、`example.com` 等数字或域名拆碎。

    Args:
        content: 原始正文。

    Returns:
        已去空白的段落列表。
    """
    raw_parts = re.split(r"(?:\n\s*\n|\n|(?<=[。！？!?])|(?<=\.)(?=\s|$))", content or "")
    return [part.strip() for part in raw_parts if part and part.strip()]


def extract_keywords(query: str, title: str = "") -> list[str]:
    """从 query 和标题中提取轻量关键词。

    Args:
        query: 检索 query。
        title: 文档标题。

    Returns:
        去重后的关键词列表。
    """
    text = f"{query} {title}".strip()
    ascii_terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", text)
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    terms: list[str] = []
    for chunk in cjk_chunks:
        if len(chunk) <= 4:
            terms.append(chunk)
            continue
        for size in (4, 3, 2):
            for index in range(0, len(chunk) - size + 1):
                terms.append(chunk[index:index + size])
    terms.extend(ascii_terms)
    seen = set()
    output = []
    for term in terms:
        normalized = term.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(term)
    return output[:30]


def _passage_score(passage: str, keywords: list[str]) -> float:
    """计算段落作为 key passage 的规则分数。

    Args:
        passage: 候选段落。
        keywords: query 和标题提取出的关键词。

    Returns:
        规则分数，值越高表示越适合作为 key passage。
    """
    lower_passage = passage.lower()
    score = 0.0
    for keyword in keywords:
        if keyword.lower() in lower_passage:
            score += 2.0
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|％|亿|万|年|月|日|美元|元)", passage):
        score += 1.5
    if 40 <= len(passage) <= MAX_PASSAGE_LENGTH:
        score += 0.5
    if len(passage) > MAX_PASSAGE_LENGTH * 2:
        score -= 1.0
    return score


def _passage_has_keyword(passage: str, keywords: list[str]) -> bool:
    """判断段落是否命中 query/title 关键词。

    Args:
        passage: 候选段落。
        keywords: query 和标题提取出的关键词。

    Returns:
        只要命中任一关键词即返回 True。
    """
    lower_passage = passage.lower()
    return any(keyword.lower() in lower_passage for keyword in keywords)


def extract_key_passages(
    content: str,
    query: str,
    title: str = "",
    max_passages: int = DEFAULT_KEY_PASSAGE_COUNT,
    max_length: int = MAX_PASSAGE_LENGTH,
) -> list[str]:
    """规则抽取 key passages，不增加额外 LLM 调用。

    Args:
        content: 原始正文或 local chunk。
        query: 当前检索 query。
        title: 文档标题。
        max_passages: 最多返回片段数。
        max_length: 单个片段最大长度。

    Returns:
        关键片段列表；无命中时返回正文前段。
    """
    passages = split_passages(content)
    if not passages:
        return []
    keywords = extract_keywords(query, title)
    scored = [
        (
            _passage_score(passage, keywords),
            index,
            passage[:max_length],
            _passage_has_keyword(passage, keywords),
        )
        for index, passage in enumerate(passages)
    ]
    matched = [item for item in scored if item[3]]
    selected = sorted(matched, key=lambda item: (-item[0], item[1]))[:max_passages]
    if not selected:
        selected = [
            (0, index, passage[:max_length], False)
            for index, passage in enumerate(passages[:max_passages])
        ]
    selected = sorted(selected, key=lambda item: item[1])
    return [item[2] for item in selected]


def build_evidence_atom(
    record: dict[str, Any],
    query: str,
    source_store: CollectorSourceStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """从搜索记录构造 evidence atom 和兼容 doc_info。

    Args:
        record: 标准化搜索记录。
        query: 当前检索 query。
        source_store: 当前 source store。

    Returns:
        `(atom, doc_info)`；atom 不包含完整正文，doc_info 保留 legacy `original_content`。
    """
    url = str(record.get("url") or "")
    title = str(record.get("title") or "Untitled")
    content = str(record.get("content") or "")[:MAX_COLLECTOR_DOC_CONTENT_LENGTH]
    source_type = (
        "local"
        if str(record.get("type") or "").lower() == "text" or url.startswith("localdataset://")
        else "web"
    )
    doc_id = generate_doc_id(url=url, title=title, source_type=source_type)
    source_id = generate_source_id(doc_id, content=content)
    stored = source_store.write(source_id, content)
    if not stored:
        logger.warning(
            "[CollectorEvidence] failed to write source_store. doc_id=%s | source_id=%s",
            doc_id,
            source_id,
        )
    content_ref = build_content_ref(doc_id=doc_id, source_id=source_id, stored=stored)
    key_passages = extract_key_passages(content=content, query=query, title=title)
    base = {
        "doc_id": doc_id,
        "source_id": source_id,
        "title": title,
        "url": url,
        "source": extract_source(url),
        "publish_time": "未提供时间信息",
        "query": query,
        "key_passages": key_passages,
        "scores": {
            "authority": None,
            "relevance": None,
            "answerability": None,
            "data_density": None,
        },
        "content_ref": content_ref,
    }
    doc_info = {**base, "original_content": content}
    normalize_doc_info_scores_and_time(doc_info)
    return base, doc_info


def _truncate_text(value: Any, max_length: int) -> str:
    """截断 evidence 文本字段。

    Args:
        value: 原始字段值。
        max_length: 最大保留长度。

    Returns:
        截断后的字符串。
    """
    text = str(value or "")
    return text[:max_length]


def _compact_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """构造不含全文的紧凑文档视图。

    Args:
        doc: 完整兼容 doc_info。

    Returns:
        不含 original_content 的 evidence 视图。
    """
    return {
        "source_id": doc.get("source_id") or doc.get("doc_id", ""),
        "doc_id": doc.get("doc_id", ""),
        "title": _truncate_text(doc.get("title", ""), 120),
        "url": _truncate_text(doc.get("url", ""), 300),
        "source": _truncate_text(doc.get("source", ""), 120),
        "publish_time": doc.get("publish_time") or doc.get("doc_time", ""),
        "query": doc.get("query", ""),
        "key_passages": [_truncate_text(passage, MAX_PASSAGE_LENGTH) for passage in doc.get("key_passages", [])],
        "scores": doc.get("scores", {}),
        "content_ref": doc.get("content_ref", {}),
    }


def _compact_supervisor_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """构造 SupervisorNode 使用的紧凑 evidence 行。

    Args:
        doc: 完整兼容 doc_info。

    Returns:
        字段级截断后的 supervisor evidence 行。
    """
    compact = _compact_doc(doc)
    return {
        "source_id": compact["source_id"],
        "doc_id": compact["doc_id"],
        "title": compact["title"],
        "source": compact["source"],
        "publish_time": compact["publish_time"],
        "key_passages": compact["key_passages"],
        "scores": compact["scores"],
    }


def build_evaluation_documents(doc_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造 doc_evaluator 的短输入。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        不含 original_content 的短输入列表。
    """
    return [_compact_doc(doc) for doc in doc_infos if isinstance(doc, dict)]


def _evidence_sort_key(doc: dict[str, Any]) -> tuple[float, float, float]:
    """构造 evidence 排序键。

    Args:
        doc: evidence 文档。

    Returns:
        relevance、answerability、data_density 三元组。
    """
    scores = doc.get("scores", {}) if isinstance(doc.get("scores"), dict) else {}
    return (
        scores.get("relevance") or 0,
        scores.get("answerability") or 0,
        scores.get("data_density") or 0,
    )


def build_supervisor_evidence_table(
    doc_infos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """构造 SupervisorNode 的 compact evidence table。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        按证据分数排序后的 compact evidence table。
    """
    ranked_docs = sorted(
        [doc for doc in doc_infos if isinstance(doc, dict)],
        key=_evidence_sort_key,
        reverse=True,
    )
    return [_compact_supervisor_doc(doc) for doc in ranked_docs]


def build_summary_evidence_pack(
    doc_infos: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造 SummaryNode 的 evidence pack。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        面向总结节点的轻量 evidence pack。
    """
    compact_docs = [_compact_doc(doc) for doc in doc_infos if isinstance(doc, dict)]
    compact_docs.sort(key=_evidence_sort_key, reverse=True)
    return {
        "sources": compact_docs,
        "source_ids": [doc.get("source_id", "") for doc in compact_docs],
    }


def _to_float_or_none(value: Any) -> float | None:
    """把 evaluator 输出转换为浮点分数。

    Args:
        value: evaluator 输出的原始分数。

    Returns:
        可解析时返回 float；缺失或非法时返回 None。
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_scores(scores: dict[str, Any] | None) -> dict[str, float | None]:
    """规范化评分字段。

    Args:
        scores: evaluator 输出的原始 scores。

    Returns:
        包含四个固定评分维度的字典。
    """
    scores = scores if isinstance(scores, dict) else {}
    return {key: _to_float_or_none(scores.get(key)) for key in SCORE_KEYS}


def normalize_doc_info_scores_and_time(doc_info: dict[str, Any]) -> dict[str, Any]:
    """Normalize structured scores and canonical time fields on doc_info."""
    scores = normalize_scores(doc_info.get("scores"))
    doc_info["scores"] = scores
    publish_time = doc_info.get("publish_time") or doc_info.get("doc_time") or "未提供时间信息"
    doc_info["publish_time"] = publish_time
    doc_info["doc_time"] = publish_time
    return doc_info
