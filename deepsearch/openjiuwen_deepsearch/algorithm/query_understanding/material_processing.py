# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""用户素材（user materials）预处理与摘要管线。

契约要点（与 docs/feature/user-materials.md 保持一致）：
- 素材单通道：content 必填（调用方直接传入正文），系统不依据 url/title 抓取。
- 摘要永远单篇独立调用，绝不跨篇混合；超过单次上限的单篇按块 map-reduce（同篇内）。
- manifest（title/url/publish_time/content_time 逐条清单）永不摘要压缩，全量进入 prompt，
  保证逐字标识符（标题、链接、时间）不丢失。
- 注入预算超限时按与 query 的相关性排序，落选者降级为单行 digest。
- 摘要按 content_hash 缓存：多轮（澄清/大纲交互/反馈）重进意图识别不重算。
- 素材文本仅作为资料，prompt 声明不执行其中的指令（防提示注入）。
"""

import asyncio
import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

# 阈值（token 为保守估算值），支持环境变量覆盖
MATERIAL_DIRECT_TOKEN_LIMIT = int(os.getenv("MATERIAL_DIRECT_TOKEN_LIMIT", "4000"))
MATERIAL_SINGLE_CALL_TOKEN_LIMIT = int(os.getenv("MATERIAL_SINGLE_CALL_TOKEN_LIMIT", "96000"))
MATERIAL_CHUNK_TOKEN_LIMIT = int(os.getenv("MATERIAL_CHUNK_TOKEN_LIMIT", "64000"))
MATERIAL_CHUNK_OVERLAP_TOKENS = int(os.getenv("MATERIAL_CHUNK_OVERLAP_TOKENS", "512"))
MATERIAL_SUMMARY_MAX_TOKENS = int(os.getenv("MATERIAL_SUMMARY_MAX_TOKENS", "4000"))
MATERIAL_INJECTION_BUDGET_TOKENS = int(os.getenv("MATERIAL_INJECTION_BUDGET_TOKENS", "200000"))
# 摘要并发上限：多篇素材同时做摘要时最多同时处理的篇数
MATERIAL_MAX_CONCURRENCY = int(os.getenv("MATERIAL_MAX_CONCURRENCY", "5"))
# 素材数量软上限：去重后保留前 N 条，超出部分记 dropped 观测（不报错、不阻断）
MATERIAL_MAX_COUNT = int(os.getenv("MATERIAL_MAX_COUNT", "50"))
# 单行 digest 摘要保留的字符数
MATERIAL_DIGEST_CHARS = 160
# 摘要失败兜底保留的 token 数（4K）：keep_chars 按 3 字符/token 保守换算
MATERIAL_FALLBACK_TRUNCATE_TOKENS = min(MATERIAL_DIRECT_TOKEN_LIMIT, 4000)

_SUMMARIZE_PROMPT = "material_summarize"
_REDUCE_PROMPT = "material_reduce_summaries"

# CJK 统一表意文字 + 假名 + 谚文
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_ALNUM_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


def estimate_text_tokens(text: str) -> int:
    """保守估算文本 token 数：CJK 约 1 字 1 token，其余按 3 字符 1 token。"""
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    other_count = len(text) - cjk_count
    return cjk_count + (other_count + 2) // 3


def content_hash(text: str) -> str:
    """素材内容的稳定哈希（用于摘要缓存命中判断）。"""
    if text is None:
        text = ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


class UserMaterial(BaseModel):
    """用户提供的一条已有信息素材。

    content 必填非空：素材内容必须由调用方直接传入，系统不依据 url/title
    主动抓取。title/url 仅作为 manifest 展示与溯源的元数据。
    """

    material_id: str = Field(default="", description="调用方自定义素材ID，用于全程追踪与去重")
    url: str = Field(default="", description="素材来源链接，可为空")
    title: str = Field(default="", description="素材标题")
    publish_time: str = Field(default="", description="素材发布/数据时间，可为空")
    content_time: str = Field(default="", description="素材内容覆盖的时间范围，可为空")
    content: str = Field(default="", description="素材正文，必填")

    @model_validator(mode="after")
    def require_content(self) -> "UserMaterial":
        for name in ("material_id", "title", "content", "url", "publish_time", "content_time"):
            setattr(self, name, (getattr(self, name) or "").strip())
        if not self.content:
            raise ValueError("user material requires non-empty content")
        return self

    @property
    def has_content(self) -> bool:
        return bool(self.content)


class MaterialManifestItem(BaseModel):
    """素材清单条目：manifest 字段永不压缩，summary 为该素材的注入内容。"""

    material_id: str = Field(default="", description="素材标识（调用方提供或系统生成 M1/M2/...）")
    title: str = Field(default="", description="素材标题")
    url: str = Field(default="", description="素材来源链接")
    publish_time: str = Field(default="", description="素材发布/数据时间")
    content_time: str = Field(default="", description="素材内容覆盖的时间范围")
    content_hash: str = Field(default="", description="content 的内容哈希（缓存键）")
    summary: str = Field(default="", description="注入 prompt 的内容：原文/摘要/digest")
    summary_kind: Literal["none", "fulltext", "summary", "digest", "truncated"] = Field(
        default="none", description="summary 的来源：none=无正文 fulltext=原文直通 summary=LLM摘要 digest=单行降级 truncated=摘要失败头部截断"
    )
    token_count: int = Field(default=0, description="summary 的估算 token 数")

    @property
    def has_content(self) -> bool:
        return bool(self.content_hash)


class MaterialEvidenceClaim(BaseModel):
    """A query-relevant claim extracted from one user material."""

    claim: str = Field(default="", max_length=500)
    scope: str = Field(default="", max_length=300)
    evidence_excerpt: str = Field(default="", max_length=600)
    confidence: Literal["high", "medium", "low"] = "medium"


class MaterialRelevance(BaseModel):
    """Intent-stage assessment of how one material can support the research query."""

    material_id: str = Field(default="")
    relevance: Literal["direct", "partial", "contextual", "irrelevant"] = "partial"
    relevant_dimensions: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    supported_claims: List[MaterialEvidenceClaim] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    research_gaps: List[str] = Field(default_factory=list)
    evidence_quality: Literal["fulltext", "summary", "digest", "truncated", "unknown"] = "unknown"


class MaterialAnalysis(BaseModel):
    """素材预处理产物：manifest + 摘要 + 预算降级记录。"""

    items: List[MaterialManifestItem] = Field(default_factory=list, description="素材清单（保持调用方顺序）")
    dropped: List[Dict[str, Any]] = Field(default_factory=list, description="被丢弃素材的观测记录（不落原文）")
    downgraded_ids: List[str] = Field(default_factory=list, description="因超出注入预算被降级为 digest 的素材ID")
    relevance_map: List[MaterialRelevance] = Field(
        default_factory=list,
        description="意图识别阶段生成的 query-素材证据映射",
    )

    @property
    def has_materials(self) -> bool:
        return bool(self.items)

    def get_item(self, material_id: str) -> Optional[MaterialManifestItem]:
        for item in self.items:
            if item.material_id == material_id:
                return item
        return None

    def analyzed_items(self) -> List[MaterialManifestItem]:
        return [item for item in self.items if item.summary_kind in ("fulltext", "summary")]

    def downgraded_items(self) -> List[MaterialManifestItem]:
        return [item for item in self.items if item.summary_kind == "digest"]


def apply_material_relevance_map(
    analysis: MaterialAnalysis,
    relevance_map: List[MaterialRelevance],
) -> MaterialAnalysis:
    """Keep only relevance records for known materials and annotate evidence quality."""
    items_by_id = {item.material_id: item for item in analysis.items if item.material_id}
    normalized: List[MaterialRelevance] = []
    seen_ids: set[str] = set()
    for entry in relevance_map:
        item = items_by_id.get(entry.material_id)
        if item is None or entry.material_id in seen_ids:
            continue
        seen_ids.add(entry.material_id)
        normalized.append(entry.model_copy(update={"evidence_quality": item.summary_kind}))
    return analysis.model_copy(update={"relevance_map": normalized})


def normalize_user_materials(raw: Any) -> Tuple[List[UserMaterial], List[Dict[str, Any]]]:
    """校验并去重素材列表，数量超过 MATERIAL_MAX_COUNT 时软截断。

    Returns:
        (合法素材列表, 丢弃记录列表)。丢弃记录仅含 index/reason/has_url/has_title/has_content
        观测字段，不含原文。
    """
    from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import canonicalize_url

    dropped: List[Dict[str, Any]] = []
    materials: List[UserMaterial] = []
    if not isinstance(raw, list):
        if raw:
            dropped.append({"index": 0, "reason": "not_a_list"})
        return materials, dropped

    seen_url = set()
    seen_hash = set()
    seen_title = set()
    seen_material_ids = set()
    kept_indices: List[int] = []
    for index, item in enumerate(raw):
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if not isinstance(item, dict):
            dropped.append({"index": index, "reason": "not_a_dict"})
            continue
        try:
            material = UserMaterial.model_validate(item)
        except Exception:
            dropped.append({
                "index": index,
                "reason": "invalid_or_empty",
                "has_url": bool(item.get("url")),
                "has_title": bool(item.get("title")),
                "has_content": bool(item.get("content")),
            })
            continue
        url = canonicalize_url(material.url) if material.url else ""
        hash_key = content_hash(material.content) if material.content else ""
        title_key = material.title.casefold()
        dup_reason = ""
        if material.material_id and material.material_id in seen_material_ids:
            dup_reason = "duplicate_material_id"
        elif url and url in seen_url:
            dup_reason = "duplicate_url"
        elif title_key and title_key in seen_title:
            dup_reason = "duplicate_title"
        elif hash_key and hash_key in seen_hash:
            dup_reason = "duplicate_content"
        if dup_reason:
            dropped.append({"index": index, "reason": dup_reason})
            continue
        if url:
            seen_url.add(url)
        if hash_key:
            seen_hash.add(hash_key)
        if title_key:
            seen_title.add(title_key)
        if material.material_id:
            seen_material_ids.add(material.material_id)
        materials.append(material)
        kept_indices.append(index)

    # 数量软上限：去重后保留前 MATERIAL_MAX_COUNT 条，超出部分记 dropped（保留有效素材最大化）
    if len(materials) > MATERIAL_MAX_COUNT:
        for kept_index in kept_indices[MATERIAL_MAX_COUNT:]:
            dropped.append({"index": kept_index, "reason": "exceeds_max_count"})
        materials = materials[:MATERIAL_MAX_COUNT]

    return materials, dropped


def _split_into_chunks(text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
    """按段落合并切分，保证块内完整段落；超长段落按句子硬切。带尾部重叠。"""
    if not text:
        return []
    max_chars = max(max_tokens * 3, 100)
    overlap_chars = min(overlap_tokens * 3, max_chars // 2)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    # 超长段落先按句子硬切
    units: List[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        sentences = re.split(r"(?<=[。！？.!?])\s*", paragraph)
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            while len(sentence) > max_chars:
                if current:
                    units.append(current)
                    current = ""
                units.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            if not sentence:
                continue
            if len(current) + len(sentence) + 1 > max_chars and current:
                units.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip() if current else sentence
        if current:
            units.append(current)

    chunks: List[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) > max_chars and current:
            chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = f"{tail}\n\n{unit}" if tail else unit
            current = current[:max_chars]
        else:
            current = candidate[:max_chars]
    if current:
        chunks.append(current)
    return chunks


def _query_tokens(query: str) -> set:
    """查询分词：拉丁词 + CJK 二元组，用于素材相关性粗排。"""
    tokens = set()
    lowered = (query or "").casefold()
    for word in _ALNUM_RE.findall(lowered):
        tokens.add(word)
    cjk_text = "".join(_CJK_RE.findall(lowered))
    for i in range(len(cjk_text) - 1):
        tokens.add(cjk_text[i:i + 2])
    return tokens


def _material_relevance_text(material: UserMaterial) -> str:
    """Return the bounded material fields used by lexical relevance checks."""
    return " ".join([
        material.title,
        material.url,
        material.content[:2000],
        material.publish_time,
        material.content_time,
    ])


def _has_comparable_script(query: str, material_text: str) -> bool:
    """Whether lexical zero-overlap is meaningful for this query/material pair.

    A Chinese query and an English-only paper have no shared lexical space even
    when they discuss the same topic.  Keep those pairs for intent recognition,
    which already performs semantic relevance assessment in its existing LLM
    call.  Hard filtering is reserved for pairs with a shared CJK or Latin
    lexical channel.
    """
    query_has_cjk = bool(_CJK_RE.search(query or ""))
    material_has_cjk = bool(_CJK_RE.search(material_text or ""))
    if query_has_cjk != material_has_cjk:
        return False
    if query_has_cjk:
        return True
    return (
        bool(_ALNUM_RE.search(query or ""))
        and bool(_ALNUM_RE.search(material_text or ""))
    )


def rank_materials_by_query(materials: List[UserMaterial], query: str) -> List[float]:
    """按与 query 的词面重叠度打分（0~1），返回与 materials 对齐的分数列表。"""
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return [0.0] * len(materials)
    scores = []
    for material in materials:
        text = _material_relevance_text(material)
        item_tokens = _query_tokens(text)
        if not item_tokens:
            scores.append(0.0)
            continue
        overlap = len(query_tokens & item_tokens)
        scores.append(min(1.0, overlap / max(len(query_tokens), 1)))
    return scores


def filter_materials_by_query(
    materials: List[UserMaterial], query: str, *, preserve_all: bool = False,
) -> Tuple[List[UserMaterial], List[Dict[str, Any]]]:
    """Conservatively filter materials that are lexically unrelated to the query.

    Filtering happens before summarization to keep plainly unrelated materials
    from consuming the LLM, prompt, and evidence budget.  A zero lexical score
    is only decisive when query and material share a comparable script. Cross-
    language pairs are retained for the existing intent-recognition LLM call.
    """
    if not materials:
        return [], []
    if preserve_all or not (query or "").strip():
        return materials, []
    scores = rank_materials_by_query(materials, query)
    kept, dropped = [], []
    for index, (material, score) in enumerate(zip(materials, scores)):
        if score > 0 or not _has_comparable_script(query, _material_relevance_text(material)):
            kept.append(material)
        else:
            dropped.append({"index": index, "reason": "irrelevant_to_query"})
    return kept, dropped


def _manifest_line(material_id: str, title: str, url: str, publish_time: str, content_time: str) -> str:
    parts = [f"[{material_id}]"]
    if title:
        parts.append(f"title={title}")
    if url:
        parts.append(f"url={url}")
    if publish_time:
        parts.append(f"publish={publish_time}")
    if content_time:
        parts.append(f"content={content_time}")
    return " | ".join(parts)


def restore_material_analysis(data: Any) -> Optional[MaterialAnalysis]:
    """从序列化 state 恢复 MaterialAnalysis；非法输入返回 None（不抛异常）。

    material_analysis 在 state 中以 model_dump dict 形式存储，多轮（澄清反馈/素材缓存）
    重新进入意图识别时用它恢复 items，按 content_hash 复用已算好的摘要。
    """
    if not data:
        return None
    if isinstance(data, MaterialAnalysis):
        return data
    if not isinstance(data, dict):
        return None
    try:
        return MaterialAnalysis.model_validate(data)
    except Exception as exc:
        logger.warning("[MATERIAL_RESTORE] invalid cached material_analysis, ignored: %s", exc)
        return None


def build_material_prompt_context(
    analysis: MaterialAnalysis | None, include_analysis: bool = True
) -> Dict[str, Any]:
    """构建 prompt 可直接消费的素材上下文字段（清单区 + 可选分析区）。

    Args:
        analysis: 素材预处理产物。
        include_analysis: 是否注入摘要正文。大纲/规划等只需清单与盘点结论的
            消费方传 False，避免摘要正文占用 prompt 预算。
    """
    empty = {
        "has_materials": False,
        "materials_count": 0,
        "materials_manifest_text": "",
        "materials_analysis_text": "",
        "materials_relevance_text": "",
        "_analysis_items": [],
    }
    if not analysis or not analysis.items:
        return empty

    manifest_lines = []
    analysis_lines = []
    for index, item in enumerate(analysis.items, start=1):
        material_id = item.material_id or f"M{index}"
        manifest_lines.append(_manifest_line(
            material_id, item.title, item.url, item.publish_time, item.content_time,
        ))
        if include_analysis and item.summary:
            kind_label = {
                "fulltext": "原文",
                "summary": "摘要",
                "digest": "要点（未深入分析）",
                "truncated": "截断原文（摘要失败降级）",
            }.get(item.summary_kind, "")
            header = f"[{material_id}]"
            if item.title:
                header += f" {item.title}"
            if kind_label:
                header += f"（{kind_label}）"
            analysis_lines.append(f"{header}\n{item.summary}")

    return {
        "has_materials": True,
        "materials_count": len(analysis.items),
        "materials_manifest_text": "\n".join(manifest_lines),
        "materials_analysis_text": "\n\n".join(analysis_lines),
        "materials_relevance_text": _format_material_relevance_map(analysis.relevance_map),
        # 供盘点节点校验 material_ids 使用，prompt 模板不引用
        "_analysis_items": list(analysis.items),
    }


def is_material_first_request(query: str, has_materials: bool) -> bool:
    """Return whether the user explicitly asks to synthesize supplied materials first."""
    if not has_materials:
        return False
    normalized = " ".join((query or "").casefold().split())
    markers = (
        "基于我提供", "基于提供", "提供的论文", "所提供的材料", "总结里面的内容", "仅根据",
        "provided material", "provided paper", "supplied material", "summarize the provided",
    )
    return any(marker in normalized for marker in markers)


def resolve_material_usage_mode(query: str, has_materials: bool) -> str:
    """Return the persisted material-use policy for the current request."""
    if not has_materials:
        return "none"
    if is_material_first_request(query, has_materials):
        return "required"
    return "supplementary"


def build_fallback_material_relevance_map(
    analysis: MaterialAnalysis,
    query: str,
    usage_mode: str,
) -> List[MaterialRelevance]:
    """Create conservative relevance records when the intent tool omits them."""
    scores = rank_materials_by_query(
        [UserMaterial(material_id=item.material_id, title=item.title, content=item.summary or "x")
         for item in analysis.items],
        query,
    )
    entries: List[MaterialRelevance] = []
    for item, score in zip(analysis.items, scores):
        is_candidate = score > 0 or usage_mode == "required"
        entries.append(MaterialRelevance(
            material_id=item.material_id,
            relevance="partial" if is_candidate else "contextual",
            supported_claims=[MaterialEvidenceClaim(
                claim="Synthesize only findings supported by this user-provided material.",
                confidence="low",
            )] if is_candidate else [],
            research_gaps=[] if is_candidate else ["Query relevance requires validation."],
            evidence_quality=item.summary_kind,
        ))
    return entries


def build_section_material_coverage(
    analysis: MaterialAnalysis | None, bindings: Any,
) -> Dict[str, Any]:
    """Build a deterministic, prompt-ready coverage review for one section.

    The review does not claim semantic proof.  It identifies material evidence
    that is available, its declared claims, and explicit gaps so the planner can
    search only where the supplied materials do not cover the chapter.
    """
    if not analysis or not isinstance(bindings, list):
        return {"has_bound_materials": False, "is_sufficient": False, "text": ""}
    relevance_by_id = {entry.material_id: entry for entry in analysis.relevance_map}
    lines: List[str] = []
    sufficient = True
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        material_id = str(binding.get("material_id") or "").strip()
        if not material_id or material_id in seen:
            continue
        seen.add(material_id)
        item = analysis.get_item(material_id)
        relevance = relevance_by_id.get(material_id)
        declared_claims = str(binding.get("claims_to_use") or "").strip()
        inferred_claims = [claim.claim for claim in (relevance.supported_claims if relevance else []) if claim.claim]
        gaps = list(relevance.research_gaps if relevance else [])
        usable = bool(item and item.summary and item.summary_kind != "none")
        has_claims = bool(declared_claims or inferred_claims)
        relevance_is_sufficient = bool(
            relevance and relevance.relevance in ("direct", "partial")
        )
        coverage_complete = all((
            usable,
            has_claims,
            not gaps,
            relevance_is_sufficient,
        ))
        if not coverage_complete:
            sufficient = False
        status = "covered" if coverage_complete else "needs_review"
        line = f"- {material_id}: {status}; role={binding.get('role') or 'supporting_evidence'}"
        if declared_claims:
            line += f"; chapter claims={declared_claims}"
        elif inferred_claims:
            line += f"; available claims={'; '.join(inferred_claims[:3])}"
        if gaps:
            line += f"; web gaps={'; '.join(gaps[:3])}"
        if not relevance_is_sufficient:
            line += "; query relevance not sufficient"
        if not usable:
            line += "; material content unavailable"
        lines.append(line)
    return {
        "has_bound_materials": bool(lines),
        "is_sufficient": bool(lines) and sufficient,
        "text": "\n".join(lines),
    }


def _format_material_relevance_map(relevance_map: List[MaterialRelevance]) -> str:
    """Render compact query-material evidence cards for outline and planner prompts."""
    lines: List[str] = []
    for entry in relevance_map:
        if entry.relevance == "irrelevant":
            continue
        parts = [f"[{entry.material_id}] relevance={entry.relevance}"]
        if entry.relevant_dimensions:
            parts.append(f"dimensions={'; '.join(entry.relevant_dimensions[:4])}")
        if entry.roles:
            parts.append(f"roles={'; '.join(entry.roles[:3])}")
        lines.append(" | ".join(parts))
        for claim in entry.supported_claims[:3]:
            claim_text = claim.claim.strip()
            if claim_text:
                lines.append(f"  claim: {claim_text}")
        if entry.limitations:
            lines.append(f"  limitations: {'; '.join(entry.limitations[:2])}")
        if entry.research_gaps:
            lines.append(f"  gaps: {'; '.join(entry.research_gaps[:3])}")
    return "\n".join(lines)


def extract_material_ids(material_context: Dict[str, Any]) -> List[str]:
    """从 build_material_prompt_context 产物中提取合法素材 ID（兼容对象与序列化 dict）。"""
    known_ids: List[str] = []
    for index, item in enumerate((material_context or {}).get("_analysis_items") or [], start=1):
        if isinstance(item, dict):
            material_id = str(item.get("material_id") or "").strip()
        else:
            material_id = str(getattr(item, "material_id", "") or "").strip()
        known_ids.append(material_id or f"M{index}")
    return known_ids


def normalize_material_id(material_id: Any, known_ids: List[str]) -> str:
    """Resolve an LLM-produced material ID to one of the known stable IDs.

    Prompt manifests render IDs as ``[M1]``.  Models sometimes copy those
    brackets into structured output, while runtime routing requires the raw
    ID.  Only unambiguous exact matches are accepted after removing one or
    more enclosing bracket pairs.
    """
    candidate = str(material_id or "").strip()
    while len(candidate) >= 2 and candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1].strip()
    if candidate in known_ids:
        return candidate
    return ""


def normalize_material_bindings(
    bindings: Any,
    known_ids: List[str],
) -> List[Dict[str, str]]:
    """Validate and normalize per-section material bindings from LLM output."""
    if not isinstance(bindings, list):
        return []
    normalized: List[Dict[str, str]] = []
    seen_ids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        material_id = normalize_material_id(binding.get("material_id"), known_ids)
        if not material_id or material_id in seen_ids:
            continue
        seen_ids.add(material_id)
        normalized.append({
            "material_id": material_id,
            "role": str(binding.get("role") or "supporting_evidence").strip(),
            "claims_to_use": str(binding.get("claims_to_use") or "").strip(),
        })
    return normalized


def _merge_cached_summaries(
    items: List[MaterialManifestItem], previous: List[MaterialManifestItem]
) -> List[MaterialManifestItem]:
    """按 content_hash 命中缓存摘要，避免多轮重算。"""
    if not previous:
        return items
    cache = {
        item.content_hash: item
        for item in previous
        if item.content_hash and item.summary_kind != "none"
    }
    merged = []
    for item in items:
        cached = cache.get(item.content_hash) if item.content_hash else None
        if cached is not None:
            item = item.model_copy(update={
                "summary": cached.summary,
                "summary_kind": cached.summary_kind,
                "token_count": cached.token_count,
            })
        merged.append(item)
    return merged


async def _invoke_material_llm(prompt_name: str, prompt_ctx: dict, llm_model_name: str) -> str:
    prompt = apply_system_prompt(prompt_name, prompt_ctx)
    llm = llm_context.get().get(llm_model_name)
    response = await llm_utils.ainvoke_llm_with_stats(
        llm,
        prompt,
        llm_type="basic",
        agent_name=AgentLlmName.INTENT_RECOGNITION.value,
        need_stream_out=False,
    )
    return (response.get("content") or "").strip()


async def _summarize_chunk(
    llm_model_name: str, chunk: str, material_title: str, query: str, is_final_merge: bool = False,
) -> str:
    return await _invoke_material_llm(_SUMMARIZE_PROMPT, {
        "material_title": material_title,
        "original_query": query,
        "chunk": chunk,
        "is_final_merge": is_final_merge,
        "SUMMARY_MAX_TOKENS": MATERIAL_SUMMARY_MAX_TOKENS,
        "CURRENT_TIME": "",
    }, llm_model_name)


async def _summarize_material(
    llm_model_name: str, material: UserMaterial, query: str,
) -> Tuple[str, bool]:
    """单篇素材摘要：单次调用或篇内 map-reduce。返回 (摘要, 是否降级截断)。"""
    title = material.title or (material.url or "用户素材")
    token_count = estimate_text_tokens(material.content)
    if token_count <= MATERIAL_SINGLE_CALL_TOKEN_LIMIT:
        summary = await _summarize_chunk(llm_model_name, material.content, title, query)
        return summary, False

    chunks = _split_into_chunks(material.content, MATERIAL_CHUNK_TOKEN_LIMIT, MATERIAL_CHUNK_OVERLAP_TOKENS)
    partials = []
    for chunk in chunks:
        partials.append(await _summarize_chunk(llm_model_name, chunk, title, query))
    if len(partials) == 1:
        return partials[0], False
    merged = await _invoke_material_llm(_REDUCE_PROMPT, {
        "material_title": title,
        "original_query": query,
        "partial_summaries": partials,
        "SUMMARY_MAX_TOKENS": MATERIAL_SUMMARY_MAX_TOKENS,
    }, llm_model_name)
    return merged, False


def _fallback_truncate(material: UserMaterial) -> str:
    """摘要失败降级：头部截断，绝不阻断主流程。"""
    keep_chars = max(MATERIAL_FALLBACK_TRUNCATE_TOKENS * 3, 200)
    return material.content[:keep_chars]


def _summary_injection_tokens(material_id: str, item: MaterialManifestItem) -> int:
    """清单行 + 摘要注入的 token 估算。"""
    manifest_chars = len(_manifest_line(
        material_id, item.title, item.url, item.publish_time, item.content_time,
    ))
    return estimate_text_tokens(item.summary) + manifest_chars // 3 + 8


async def prepare_material_analysis(
    raw_materials: Any,
    llm_model_name: str = "",
    query: str = "",
    previous_items: Optional[List[MaterialManifestItem]] = None,
) -> MaterialAnalysis:
    """素材预处理入口：校验/去重 -> 摘要（带缓存）-> 预算降级。

    Args:
        raw_materials: UserMaterial dict 列表。
        llm_model_name: 摘要使用的 LLM 槽位名。
        query: 用户原始 query，用于超预算时的相关性排序。
        previous_items: 上一轮 MaterialAnalysis.items（按 content_hash 复用摘要）。

    Returns:
        MaterialAnalysis。失败素材按头部截断降级，不抛异常。
    """
    materials, dropped = normalize_user_materials(raw_materials)
    materials, irrelevant = filter_materials_by_query(
        materials,
        query,
        preserve_all=is_material_first_request(query, bool(materials)),
    )
    dropped.extend(irrelevant)
    if dropped:
        logger.info("[MATERIAL_PREPARE] dropped=%d", len(dropped))
    if not materials:
        return MaterialAnalysis(items=[], dropped=dropped)

    items: List[MaterialManifestItem] = []
    # Reserve caller-supplied IDs before allocating M<N> IDs for unlabelled
    # materials, so a later explicit M1 cannot collide with an earlier blank
    # material that would otherwise be assigned M1.
    reserved_ids = {material.material_id for material in materials if material.material_id}
    assigned_ids: set[str] = set()
    next_auto_id = 1
    for index, material in enumerate(materials):
        material_id = material.material_id
        if not material_id:
            while f"M{next_auto_id}" in reserved_ids or f"M{next_auto_id}" in assigned_ids:
                next_auto_id += 1
            material_id = f"M{next_auto_id}"
            next_auto_id += 1
        assigned_ids.add(material_id)
        items.append(MaterialManifestItem(
            material_id=material_id,
            title=material.title,
            url=material.url,
            publish_time=material.publish_time,
            content_time=material.content_time,
            content_hash=content_hash(material.content) if material.content else "",
        ))

    items = _merge_cached_summaries(items, previous_items or [])

    # 需要摘要的素材（有正文且缓存未命中）
    pending: List[Tuple[int, UserMaterial]] = []
    for index, material in enumerate(materials):
        item = items[index]
        if material.content and item.summary_kind not in ("fulltext", "summary"):
            pending.append((index, material))

    if pending:
        semaphore = asyncio.Semaphore(max(MATERIAL_MAX_CONCURRENCY, 1))

        async def _summarize_one(index: int, material: UserMaterial) -> None:
            async with semaphore:
                try:
                    if estimate_text_tokens(material.content) <= MATERIAL_DIRECT_TOKEN_LIMIT:
                        items[index].summary = material.content
                        items[index].summary_kind = "fulltext"
                    else:
                        summary, _ = await _summarize_material(llm_model_name, material, query)
                        items[index].summary = summary
                        items[index].summary_kind = "summary"
                except Exception as exc:
                    if LogManager.is_sensitive():
                        logger.warning("[MATERIAL_PREPARE] material summarize failed (redacted).")
                    else:
                        logger.warning(
                            "[MATERIAL_PREPARE] material %s summarize failed: %s",
                            items[index].material_id,
                            exc,
                        )
                    summary = _fallback_truncate(material)
                    items[index].summary = summary
                    items[index].summary_kind = "truncated"

        await asyncio.gather(*(_summarize_one(index, material) for index, material in pending))

    for index, item in enumerate(items):
        item.token_count = _summary_injection_tokens(item.material_id or f"M{index + 1}", item)

    # 注入预算控制：相关性排序，超预算者降级为 digest（不依赖 LLM，确定性降级）
    scores = rank_materials_by_query(materials, query)
    ranked = sorted(range(len(items)), key=lambda i: scores[i], reverse=True)
    budget = max(MATERIAL_INJECTION_BUDGET_TOKENS, 1)
    used = 0
    downgraded_ids: List[str] = []
    for index in ranked:
        item = items[index]
        if item.summary_kind == "none":
            continue
        if used + item.token_count <= budget:
            used += item.token_count
            continue
        digest_source = item.summary or (materials[index].title or "")
        item.summary = (
            (digest_source[:MATERIAL_DIGEST_CHARS] + ("…" if len(digest_source) > MATERIAL_DIGEST_CHARS else ""))
            or item.title
        )
        item.summary_kind = "digest"
        item.token_count = _summary_injection_tokens(item.material_id, item)
        downgraded_ids.append(item.material_id)
    if downgraded_ids:
        logger.info(
            "[MATERIAL_PREPARE] budget_downgraded=%d budget_used=%d",
            len(downgraded_ids), used,
        )

    return MaterialAnalysis(items=items, dropped=dropped, downgraded_ids=downgraded_ids)


def build_material_evidence_items(
    analysis: MaterialAnalysis | Dict[str, Any] | None,
    material_ids: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """把素材分析条目转为写作证据条目（classified_content / citation 兼容形态）。

    - 仅收录有实质注入内容的素材（summary_kind ∈ {fulltext, summary, digest}）；
      无 content 素材在校验阶段即被拒绝，不会出现在 analysis 中。
    - original_content 使用原文直通（≤4K）或摘要（≤1K）内容，绝不把超长原文带进写作上下文。
    """
    if not analysis:
        return []
    if isinstance(analysis, dict):
        try:
            analysis = MaterialAnalysis.model_validate(analysis)
        except Exception:
            return []
    wanted = {mid for mid in (material_ids or []) if mid} if material_ids is not None else None
    items: List[Dict[str, Any]] = []
    for entry in analysis.items:
        if wanted is not None and entry.material_id not in wanted:
            continue
        content = (entry.summary or "").strip()
        if not content or entry.summary_kind not in ("fulltext", "summary", "digest", "truncated"):
            continue
        items.append({
            "index": 0,  # 由合并方统一编号
            "title": entry.title or "用户提供的资料",
            "original_content": content,
            "passage_text": content,
            "scores": 1.0,
            "reliability": 0.8,
            "data_density": 0.0,
            "is_fulltext": entry.summary_kind == "fulltext",
            "url": entry.url or "",
            "publish_time": entry.publish_time or "",
            "content_time": entry.content_time or "",
            "source": "user_material",
            "material_id": entry.material_id,
            "summary_kind": entry.summary_kind,
        })
    return items


def resolve_material_evidence(current_inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按规划声明解析当前章节应并入写作证据的用户素材条目。

    - 优先使用 planner 的 `use_material_ids`；
    - 否则从章节的 `material_bindings` 派生 ID，并精确过滤到该集合；
    - 没有绑定时不并入素材，避免把无关素材塞进章节证据。
    """
    analysis = restore_material_analysis(current_inputs.get("material_analysis"))
    if not analysis:
        return []
    declared = [mid for mid in (current_inputs.get("use_material_ids") or []) if mid]
    if not declared:
        declared = [
            binding.get("material_id")
            for binding in current_inputs.get("material_bindings") or []
            if isinstance(binding, dict) and binding.get("material_id")
        ]
    declared = list(dict.fromkeys(declared))
    if declared:
        return build_material_evidence_items(analysis, declared)
    return []


def format_section_material_bindings(
    bindings: Any,
    material_ids: Optional[List[str]] = None,
) -> str:
    """Render the current section's material-use contract for the writer prompt.

    A material can be bound to more than one section.  This function deliberately
    has no global state and only renders bindings selected for the current section,
    so parallel writers receive independent, scoped instructions.
    """
    if not isinstance(bindings, list):
        return ""
    selected_ids = set(material_ids) if material_ids is not None else None
    lines: List[str] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        material_id = str(binding.get("material_id") or "").strip()
        if not material_id or (selected_ids is not None and material_id not in selected_ids):
            continue
        role = str(binding.get("role") or "supporting_evidence").strip()
        claims = str(binding.get("claims_to_use") or "").strip()
        line = f"- {material_id}: role={role}"
        if claims:
            line += f"; allowed claims={claims}"
        lines.append(line)
    return "\n".join(lines)
