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
#: CJK 统一表意文字，用于判断句子间是否需要空格分隔。
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _join_sentences(sentences: list[str]) -> str:
    """CJK 句子间无空格，拉丁文句子间需空格分隔。"""
    if all(_CJK_RE.search(s) for s in sentences):
        return "".join(sentences)
    return " ".join(sentences)


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


def split_passages(content: str, max_length: int = 500, overlap: int = 200) -> list[str]:
    """把正文切分为结构化的段落。

    依据 COINS 2025 基准实验推荐：
    - 以句子级切分为主要策略（优于固定长度/语义切分）
    - 窗口大小 max_length（默认 500 字符，约 512 token）
    - 片段间保留 overlap（默认 200 字符）的上下文重叠

    流程：
    1. 按空行分为块
    2. 表格/列表块保持完整（超长时按行切分，保留表头）
    3. 普通块：按句号切分 → 句子级贪心累积到 max_length → 片段间保留 overlap 重叠
    4. 合并短片段（< 40 字符）到上一段

    Args:
        content: 原始正文。
        max_length: 单个段落最大字符数。
        overlap: 片段间重叠字符数。

    Returns:
        已去空白的段落列表。
    """
    if not content or not content.strip():
        return []

    # Step 1: 按空行分段
    raw_blocks = re.split(r"\n\s*\n", content)
    blocks: list[str] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        blocks.append(block)

    # Step 2: 对每个块应用句子级切分
    passages: list[str] = []
    for block in blocks:
        lines = block.split("\n")

        # 表格/列表块：保持完整或按行切分
        if _is_markdown_table(block) or _is_structured_block(lines):
            if len(block) <= max_length:
                passages.append(block)
            elif _is_markdown_table(block):
                passages.extend(_split_long_table(block, max_length))
            else:
                passages.append(block)  # 列表块不拆
            continue

        # 普通文本块：句子级切分 + 滑动窗口
        sentences = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Z])", block)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            continue

        # CJK 句子间无空格，拉丁文句子间需空格分隔
        current: list[str] = []
        for sentence in sentences:
            if current and len(_join_sentences(current + [sentence])) > max_length:
                passages.append(_join_sentences(current))
                # 从末尾向前贪心取 overlap 字符的句子作为重叠
                overlap_sentences: list[str] = []
                overlap_len = 0
                for prev_sentence in reversed(current):
                    if overlap_len + len(prev_sentence) > overlap:
                        break
                    overlap_sentences.insert(0, prev_sentence)
                    overlap_len += len(prev_sentence)
                current = list(overlap_sentences) + [sentence] if overlap_sentences else [sentence]
            else:
                current.append(sentence)

        if current:
            passages.append(_join_sentences(current))

    # Step 3: 合并短片段（< 40 字符）到上一段
    merged: list[str] = []
    for passage in passages:
        passage = passage.strip()
        if not passage:
            continue
        if merged and len(passage) < 40:
            merged[-1] = merged[-1] + "\n" + passage
        else:
            merged.append(passage)

    return merged


def _is_structured_block(lines: list[str]) -> bool:
    """检测是否是表格/列表块，如果是则不应拆分。

    判定规则：超过一半的行包含表格/列表特征。
    """
    if len(lines) < 3:
        return False
    structured_count = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 表格行（含 |）
        if "|" in line:
            structured_count += 1
        # Markdown 表格分隔行
        elif re.match(r"^\|?[\s\-:|]+\|?$", line):
            structured_count += 1
        # 列表项（1. / - / * / 数字.）
        elif re.match(r"^\d+[.、)]", line) or re.match(r"^[-*•]", line):
            structured_count += 1
    total = max(1, len([line for line in lines if line.strip()]))
    return structured_count / total > 0.5


def _is_markdown_table(passage: str) -> bool:
    """检测段落是否是 Markdown 表格。

    Markdown 表格的结构：
    | 列1 | 列2 |
    | --- | --- |
    | 数据1 | 数据2 |
    """
    lines = passage.strip().split("\n")
    if len(lines) < 3:
        return False
    # 第一行必须含 |（表头行）
    if "|" not in lines[0]:
        return False
    # 第二行必须是分隔行（|---|---| 或 |:---|:---:|）
    if not re.match(r"^\|?[\s\-:|]+\|?$", lines[1].strip()):
        return False
    # 至少有一行数据行（含 |）
    return any("|" in line for line in lines[2:])


def _split_long_table(passage: str, max_length: int = 500) -> list[str]:
    """将超长 Markdown 表格按行切分，每个片段保留表头。

    确保表格结构不丢失：每个片段都包含完整的表头行和分隔行，
    数据行按 max_length 预算贪心累积。
    """
    lines = passage.strip().split("\n")
    # 提取表头行 + 分隔行（前两行）
    header_lines = lines[:2]
    data_lines = lines[2:]
    header_block = "\n".join(header_lines)
    header_len = len(header_block)

    # 如果表头本身已超限，无法安全切分，回退到硬截断
    if header_len >= max_length:
        return [passage[:max_length]]

    result: list[str] = []
    current_rows: list[str] = []
    current_len = header_len

    for row in data_lines:
        row_len = len(row) + 1  # +1 for \n
        if current_rows and current_len + row_len > max_length:
            # 当前片段已满，输出
            result.append(header_block + "\n" + "\n".join(current_rows))
            current_rows = [row]
            current_len = header_len + row_len
        else:
            current_rows.append(row)
            current_len += row_len

    if current_rows:
        result.append(header_block + "\n" + "\n".join(current_rows))

    return result if result else [passage[:max_length]]


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


def _passage_score(passage: str, keywords: list[str], max_length: int = MAX_PASSAGE_LENGTH) -> float:
    """计算段落作为 key passage 的规则分数。

    Args:
        passage: 候选段落。
        keywords: query 和标题提取出的关键词。
        max_length: 单个片段最大长度，用于评分阈值判断。

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
    if 40 <= len(passage) <= max_length:
        score += 0.5
    if len(passage) > max_length * 2:
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
    passages = split_passages(content, max_length=max_length)
    if not passages:
        return []
    keywords = extract_keywords(query, title)
    scored = [
        (
            _passage_score(passage, keywords, max_length),
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
    full_text = str(record.get("full_text") or "")
    use_full_text = record.get("full_text_status") == "available" and bool(full_text.strip())
    content = str(full_text if use_full_text else record.get("content") or "")[
        :MAX_COLLECTOR_DOC_CONTENT_LENGTH
    ]
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
    date_metadata = record.get("date_metadata") or {}
    canonical_publish_time = ""
    if date_metadata.get("type") == "published":
        canonical_publish_time = str(date_metadata.get("parsed_date") or "")
        if not canonical_publish_time and date_metadata.get("precision") in {"year", "month"}:
            canonical_publish_time = str(date_metadata.get("value") or "")
    # Search APIs report a relevance score (0-1) on their normalized records;
    # expose it as scores.relevance so downstream consumers (e.g. enrichment
    # candidate ranking) can sort without degrading to insertion order.
    scores_record = record.get("scores")
    relevance_val = None
    if isinstance(scores_record, dict) and scores_record:
        evidence_scores = dict(scores_record)
        relevance_val = scores_record.get("relevance")
    else:
        evidence_scores = {}
    if relevance_val is None:
        raw_score = record.get("score")
        if isinstance(raw_score, (int, float)):
            relevance_val = raw_score
    if isinstance(relevance_val, (int, float)):
        evidence_scores["relevance"] = max(0.0, min(float(relevance_val), 1.0))
    elif "relevance" not in evidence_scores:
        evidence_scores["relevance"] = 0.0
    base = {
        "doc_id": doc_id,
        "source_id": source_id,
        "title": title,
        "url": url,
        "source": extract_source(url),
        "publish_time": canonical_publish_time or "",
        "doc_time": canonical_publish_time or "",
        "query": query,
        "key_passages": key_passages,
        "content_ref": content_ref,
        "scores": evidence_scores,
        "evidence_content_type": "full_text" if use_full_text else "abstract",
        "evidence_content_chars": len(content),
    }
    if record.get("skip_webpage_enrichment") is True:
        base["skip_webpage_enrichment"] = True
    for key in (
        "academic_source",
        "academic_source_id",
        "doi",
        "pmid",
        "pmcid",
        "arxiv_id",
        "matched_sources",
        "source_ids",
        "full_text_candidates",
        "full_text_status",
        "content_type",
        "full_text_format",
        "full_text_url",
        "full_text_truncated",
    ):
        if key in record:
            base[key] = record[key]
    doc_info = {**base, "original_content": content}
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
    result = {
        "source_id": doc.get("source_id") or doc.get("doc_id", ""),
        "doc_id": doc.get("doc_id", ""),
        "title": _truncate_text(doc.get("title", ""), 120),
        "url": _truncate_text(doc.get("url", ""), 300),
        "source": _truncate_text(doc.get("source", ""), 120),
        "publish_time": doc.get("publish_time") or doc.get("doc_time", ""),
        "query": doc.get("query", ""),
        "key_passages": [_truncate_text(passage, MAX_PASSAGE_LENGTH) for passage in doc.get("key_passages", [])],
        "content_ref": doc.get("content_ref", {}),
    }
    if doc.get("scores"):
        result["scores"] = doc["scores"]
    for key in (
        "academic_source",
        "academic_source_id",
        "doi",
        "pmid",
        "pmcid",
        "arxiv_id",
        "matched_sources",
        "source_ids",
        "evidence_content_type",
        "evidence_content_chars",
        "full_text_format",
        "full_text_url",
        "full_text_truncated",
    ):
        if key in doc:
            result[key] = doc[key]
    return result


def _compact_supervisor_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """构造 SupervisorNode 使用的紧凑 evidence 行。

    Args:
        doc: 完整兼容 doc_info。

    Returns:
        字段级截断后的 supervisor evidence 行。
    """
    compact = _compact_doc(doc)
    result = {
        "source_id": compact["source_id"],
        "doc_id": compact["doc_id"],
        "title": compact["title"],
        "source": compact["source"],
        "publish_time": compact["publish_time"],
        "key_passages": compact["key_passages"],
    }
    if doc.get("scores"):
        result["scores"] = doc["scores"]
    return result


def build_evaluation_documents(doc_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造 doc_evaluator 的短输入。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        不含 original_content 的短输入列表。
    """
    return [_compact_doc(doc) for doc in doc_infos if isinstance(doc, dict)]


def build_supervisor_evidence_table(
    doc_infos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """构造 SupervisorNode 的 compact evidence table。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        compact evidence table。
    """
    ranked_docs = [doc for doc in doc_infos if isinstance(doc, dict)]
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
    return {
        "sources": compact_docs,
        "source_ids": [doc.get("source_id", "") for doc in compact_docs],
    }
