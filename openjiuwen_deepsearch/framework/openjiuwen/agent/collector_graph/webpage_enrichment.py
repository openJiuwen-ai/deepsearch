# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from typing import Any

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import extract_key_passages
from openjiuwen_deepsearch.algorithm.research_collector.article_link_follow import (
    ARTICLE_LINK_SOURCE_COUNT_FIELD,
    ARTICLE_LINK_SOURCE_FIELD,
    ArticleLinkCandidate,
    ArticleLinkCandidateBuildStats,
    ArticleLinkEvidence,
    build_article_link_source_with_count,
    build_article_link_candidates,
    count_article_links,
    select_article_link_candidates,
)
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    CollectorSourceStore,
    build_evaluation_documents,
    build_evidence_atom,
    canonicalize_url,
    normalize_doc_info_scores_and_time,
    normalize_scores,
)
from openjiuwen_deepsearch.algorithm.research_collector.doc_evaluation import run_doc_evaluation
from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import (
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    MIN_FETCHED_CONTENT_LENGTH,
    WebPageEnrichmentDecision,
    WebPageEvidenceContent,
    apply_enrichment_to_doc,
    build_compression_user_payload,
    build_enrichment_candidates,
    build_selection_user_payload,
    capture_doc_identity,
    coerce_fetch_timeout_seconds,
    find_matching_doc_index,
    has_pdf_magic,
    has_sufficient_fetched_content,
    is_explicit_pdf_url,
    sanitize_selected_indexes,
    should_replace_original_content,
    synchronize_history_queries,
    truncate_raw_content_for_compression,
)
from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    append_link_attempts,
    ensure_ledger,
)
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
    WebFetchWebpageAdapter,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, record_llm_retry_log
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_public_web_url
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context, session_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)
MAX_SELECTION_CANDIDATES = 10
_ARTICLE_LINK_LOG_PREFIX = "[ArticleLinkFollow]"


def _remaining_timeout_seconds(deadline: float) -> int:
    """计算整体抓取 deadline 剩余的请求超时秒数。

    Args:
        deadline: event loop 单调时钟上的绝对截止时间。

    Returns:
        传给同步 HTTP 请求的正整数秒数；整体上限由外层 asyncio deadline 保证。
    """
    remaining = deadline - asyncio.get_running_loop().time()
    return max(1, math.ceil(remaining))


@dataclass(frozen=True)
class QualityRejectionLogContext:
    """封装质量门禁拒绝事件的关联日志信息。

    Attributes:
        section_idx: 当前章节索引。
        step_title: 当前采集步骤标题。
        original_doc: 增强前文档。
        quality_reason: 质量门禁拒绝原因。
        fetched: 网页抓取结果。
        evidence: 压缩后的候选证据。
    """

    section_idx: int
    step_title: str
    original_doc: dict[str, Any]
    quality_reason: str
    fetched: dict[str, Any]
    evidence: WebPageEvidenceContent


@dataclass(frozen=True)
class FollowedArticle:
    """成功构造的链接跟进文档及其 source store 正文。"""

    candidate_index: int
    canonical_url: str
    doc_info: dict[str, Any]
    source_content: str


@dataclass
class ArticleLinkFollowStats:
    """一次文章链接跟进调用的本地诊断计数。"""

    parent_doc_count: int = 0
    raw_candidate_count: int = 0
    safe_candidate_count: int = 0
    unsafe_count: int = 0
    selected_count: int = 0
    fetch_success_count: int = 0
    compression_success_count: int = 0
    evaluation_success_count: int = 0
    writeback_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0


@dataclass(frozen=True)
class ArticleLinkWritebackStats:
    """集中写回阶段的结果计数。"""

    attempted_count: int
    successful_count: int
    failed_count: int
    duplicate_count: int


class WebPageEnrichmentNode(BaseNode):
    """选择性抓取并压缩本轮网页搜索结果。"""

    def __init__(self):
        """初始化网页正文增强节点。"""
        super().__init__()
        self.llm: Any = None

    @staticmethod
    def _log_article_link_item(
        *,
        section_idx: int,
        stage: str,
        outcome: str,
        candidate: ArticleLinkCandidate,
        reason: str = "",
        error: BaseException | None = None,
    ) -> None:
        """记录单链接阶段，不输出网页正文或异常正文。"""
        error_type = type(error).__name__ if error is not None else "none"
        if LogManager.is_sensitive():
            logger.info(
                "%s phase=follow_item section_idx=%s stage=%s outcome=%s "
                "candidate_index=%s error_type=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                section_idx,
                stage,
                outcome,
                candidate.candidate_index,
                error_type,
            )
            return
        logger.info(
            "%s phase=follow_item section_idx=%s stage=%s outcome=%s "
            "candidate_index=%s url=%s parent_url=%s anchor_text=%s "
            "selection_reason=%s error_type=%s",
            _ARTICLE_LINK_LOG_PREFIX,
            section_idx,
            stage,
            outcome,
            candidate.candidate_index,
            candidate.url,
            candidate.parent_url,
            candidate.anchor_text,
            reason,
            error_type,
        )

    async def _invoke_structured_llm(
        self,
        *,
        state: dict,
        prompt_name: str,
        payload: dict,
        agent_name: str,
        schema: type,
        operation: str,
    ) -> Any | None:
        """执行 A/B 共用的结构化 LLM 调用与失败降级。"""
        messages = [
            *apply_system_prompt(prompt_name, {}),
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            return await ainvoke_llm_with_stats(
                self.llm,
                messages,
                agent_name=agent_name,
                schema=schema,
            )
        except Exception as exc:
            record_llm_retry_log(
                current_try=1,
                max_retries=1,
                section_idx=state.get("section_idx", 0),
                step_title=state.get("step_title", ""),
                error=exc,
                operation=operation,
            )
            return None

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        """读取网页增强节点需要的运行状态。

        Args:
            inputs: 当前节点输入。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            节点运行状态字典。
        """
        section_idx = session.get_global_state("collector_context.section_idx")
        step_title = session.get_global_state("collector_context.step_title")
        webpage_enrichment_enabled = bool(
            session.get_global_state("config.info_collector_webpage_enrich_enable")
        )
        article_link_follow_enabled = bool(
            session.get_global_state("config.info_collector_article_link_follow_enable")
        )
        self.llm = None
        if webpage_enrichment_enabled or article_link_follow_enabled:
            llm_model_name = adapt_llm_model_name(session, NodeId.INFO_COLLECTOR.value)
            self.llm = llm_context.get().get(llm_model_name)
        return {
            "enabled": webpage_enrichment_enabled,
            "webpage_enrichment_enabled": webpage_enrichment_enabled,
            "article_link_follow_enabled": article_link_follow_enabled,
            "max_urls": session.get_global_state("config.info_collector_webpage_enrich_max_urls") or 3,
            "webpage_enrichment_max_urls": (
                session.get_global_state("config.info_collector_webpage_enrich_max_urls") or 3
            ),
            "article_link_follow_max_urls": (
                session.get_global_state("config.info_collector_article_link_follow_max_urls") or 3
            ),
            "fetch_timeout_seconds": session.get_global_state(
                "config.info_collector_webpage_enrich_fetch_timeout_seconds"
            ),
            "section_idx": section_idx,
            "plan_title": session.get_global_state("collector_context.plan_title") or "",
            "plan_thought": session.get_global_state("collector_context.plan_thought") or "",
            "step_title": step_title,
            "step_description": session.get_global_state("collector_context.step_description") or "",
            "new_doc_infos_current_loop": (
                session.get_global_state("collector_context.new_doc_infos_current_loop") or []
            ),
            "doc_infos": session.get_global_state("collector_context.doc_infos") or [],
            "history_queries": session.get_global_state("collector_context.history_queries") or [],
            "source_store": session.get_global_state("collector_context.source_store") or {},
            "evidence_ledger": ensure_ledger(
                session.get_global_state("collector_context.evidence_ledger")
            ),
            "session": session,
        }

    async def _select_candidate_indexes(self, state: dict) -> list[int]:
        """调用 LLM 选择需要抓取正文的候选索引。

        Args:
            state: 节点运行状态，必须包含 candidates 和 max_urls。

        Returns:
            清洗后的候选列表索引。
        """
        candidates = state.get("candidates", [])
        if not candidates:
            return []
        user_payload = build_selection_user_payload(state, candidates)
        decision = await self._invoke_structured_llm(
            state=state,
            prompt_name="collector_webpage_enrichment_select",
            payload=user_payload,
            agent_name=AgentLlmName.COLLECTOR_WEBPAGE_ENRICHMENT_SELECTION.value,
            schema=WebPageEnrichmentDecision,
            operation="select webpage enrichment candidates",
        )
        if decision is None:
            return []
        return sanitize_selected_indexes(
            decision.selected_indexes,
            candidate_count=len(candidates),
            max_urls=state.get("max_urls", 3),
        )

    async def _compress_content(self, state: dict, doc_info: dict, fetched: dict) -> WebPageEvidenceContent | None:
        """把抓取正文压缩为 bounded original_content。

        Args:
            state: 节点运行状态。
            doc_info: 当前候选对应的 doc_info。
            fetched: `fetch_webpage_sync` 返回的结构化抓取结果。

        Returns:
            压缩后的证据内容；失败时返回 None。
        """
        raw_content = str(fetched.get("content") or "")
        if not raw_content.strip():
            return None
        user_payload = build_compression_user_payload(state, doc_info, fetched)
        evidence = await self._invoke_structured_llm(
            state=state,
            prompt_name="collector_webpage_enrichment_compress",
            payload=user_payload,
            agent_name=AgentLlmName.COLLECTOR_WEBPAGE_ENRICHMENT_COMPRESSION.value,
            schema=WebPageEvidenceContent,
            operation="compress fetched webpage content",
        )
        if evidence is None:
            return None
        original_content = str(evidence.original_content or "")[:MAX_COLLECTOR_DOC_CONTENT_LENGTH].strip()
        if not original_content:
            return None
        key_passages = [
            str(passage or "").strip()
            for passage in (evidence.key_passages or [])
            if str(passage or "").strip()
        ][:5]
        if not key_passages:
            key_passages = extract_key_passages(
                content=original_content,
                query=str(doc_info.get("query") or ""),
                title=str(doc_info.get("title") or ""),
            )
        return WebPageEvidenceContent(original_content=original_content, key_passages=key_passages)

    def _log_fetch_event(
        self,
        level: int,
        category: str,
        url: str,
        *,
        content_len: int | None = None,
        required_len: int | None = None,
        exc: Exception | None = None,
    ) -> None:
        """记录抓取事件，并在敏感模式下移除 URL 和异常正文。

        Args:
            level: Python logging 级别。
            category: 固定抓取事件分类。
            url: 非敏感模式下用于定位的目标 URL。
            content_len: 抓取正文长度。
            required_len: 当前动态最低正文长度。
            exc: 抓取异常；仅非敏感模式记录。

        Returns:
            None.
        """
        if LogManager.is_sensitive():
            logger.log(
                level,
                "[WebPageEnrichmentNode] fetch event. category=%s content_len=%s required_len=%s",
                category,
                content_len,
                required_len,
            )
            return
        logger.log(
            level,
            "[WebPageEnrichmentNode] fetch event. category=%s url=%s content_len=%s required_len=%s error=%s",
            category,
            url,
            content_len,
            required_len,
            exc,
        )

    def _log_quality_rejection(self, context: QualityRejectionLogContext) -> None:
        """记录质量门禁拒绝事件，并在敏感模式下隐藏证据细节。

        Args:
            context: 质量门禁拒绝事件的关联日志信息。

        Returns:
            None.
        """
        original_len = len(str(context.original_doc.get("original_content") or ""))
        fetched_len = len(str(context.fetched.get("content") or ""))
        enriched_len = len(context.evidence.original_content)
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [WebPageEnrichmentNode] quality guard rejected enrichment. "
                "category=quality_guard_rejected | original_len=%s | fetched_len=%s | enriched_len=%s",
                context.section_idx,
                original_len,
                fetched_len,
                enriched_len,
            )
            return
        logger.info(
            "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] "
            "enrichment skipped by quality guard. doc_id=%s | url=%s | reason=%s | "
            "original_len=%s | fetched_len=%s | enriched_len=%s",
            context.section_idx,
            context.step_title,
            context.original_doc.get("doc_id", ""),
            context.original_doc.get("url", ""),
            context.quality_reason,
            original_len,
            fetched_len,
            enriched_len,
        )

    def _log_enrichment_success(
        self,
        state: dict,
        original_doc: dict,
        enriched_doc: dict,
        fetched: dict,
        evidence: WebPageEvidenceContent,
    ) -> None:
        """记录增强成功事件，并在敏感模式下隐藏任务和文档标识。

        Args:
            state: 网页增强节点运行状态。
            original_doc: 增强前文档。
            enriched_doc: 增强后文档。
            fetched: 网页抓取结果。
            evidence: 压缩后的证据。

        Returns:
            None.
        """
        original_len = len(str(original_doc.get("original_content") or ""))
        raw_len = len(str(fetched.get("content") or ""))
        compressed_len = len(evidence.original_content)
        key_passage_count = len(evidence.key_passages)
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [WebPageEnrichmentNode] enrichment succeeded. "
                "category=enrichment_succeeded | original_len_before=%s | raw_len=%s | "
                "compressed_len=%s | key_passages=%s",
                state.get("section_idx", 0),
                original_len,
                raw_len,
                compressed_len,
                key_passage_count,
            )
            return
        logger.info(
            "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] fetch enriched doc. "
            "doc_id=%s | source_id=%s | status_code=%s | original_len_before=%s | "
            "raw_len=%s | compressed_len=%s | key_passages=%s | scores=%s",
            state.get("section_idx", 0),
            state.get("step_title", ""),
            enriched_doc.get("doc_id", ""),
            enriched_doc.get("source_id", ""),
            fetched.get("status_code", ""),
            original_len,
            raw_len,
            compressed_len,
            key_passage_count,
            enriched_doc.get("scores", {}),
        )

    async def _fetch_webpage(
        self,
        url: str,
        timeout_seconds: int,
        minimum_content_length: int = MIN_FETCHED_CONTENT_LENGTH,
    ) -> dict:
        """异步抓取网页正文。

        Args:
            url: 目标网页 URL。
            timeout_seconds: 抓取超时时间。
            minimum_content_length: 抓取正文最低长度，至少使用固定安全门槛。

        Returns:
            抓取结果字典；失败时返回空字典。
        """
        required_length = max(MIN_FETCHED_CONTENT_LENGTH, int(minimum_content_length))
        deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
        try:
            async with asyncio.timeout_at(deadline):
                return await self._fetch_webpage_before_deadline(url, deadline, required_length)
        except TimeoutError:
            self._log_fetch_event(
                logging.WARNING,
                "fetch_deadline_exceeded",
                url,
                required_len=required_length,
            )
            return {}

    async def _fetch_webpage_before_deadline(
        self,
        url: str,
        deadline: float,
        required_length: int,
    ) -> dict:
        """在既定 deadline 内执行 direct、PDF 和 Jina fallback。

        Args:
            url: 目标网页 URL。
            deadline: event loop 单调时钟上的整体截止时间。
            required_length: 抓取正文动态最低长度。

        Returns:
            抓取成功时返回结构化结果，否则返回空字典。
        """
        direct_result: dict = {}
        explicit_pdf_url = is_explicit_pdf_url(url)
        if explicit_pdf_url:
            self._log_fetch_event(
                logging.INFO,
                "pdf_url_detected",
                url,
                required_len=required_length,
            )
        else:
            try:
                direct_result = await asyncio.to_thread(
                    WebFetchWebpageAdapter.fetch_webpage_sync,
                    url,
                    _remaining_timeout_seconds(deadline),
                )
            except Exception as exc:
                self._log_fetch_event(
                    logging.WARNING,
                    "direct_fetch_failed",
                    url,
                    required_len=required_length,
                    exc=exc,
                )

        direct_pdf_payload = has_pdf_magic(direct_result)
        if not direct_pdf_payload and has_sufficient_fetched_content(direct_result, required_length):
            direct_result["fetch_method"] = "harness_webpage_fetch"
            return direct_result

        if direct_pdf_payload:
            self._log_fetch_event(
                logging.INFO,
                "direct_pdf_payload",
                url,
                content_len=len(str(direct_result.get("content") or "")),
                required_len=required_length,
            )
        elif not explicit_pdf_url:
            self._log_fetch_event(
                logging.INFO,
                "direct_content_short",
                url,
                content_len=len(str(direct_result.get("content") or "")),
                required_len=required_length,
            )
        try:
            jina_result = await asyncio.to_thread(
                WebFetchWebpageAdapter.fetch_via_jina_reader_sync,
                url,
                _remaining_timeout_seconds(deadline),
            )
        except Exception as exc:
            self._log_fetch_event(
                logging.WARNING,
                "jina_fetch_failed",
                url,
                required_len=required_length,
                exc=exc,
            )
            return {}
        if has_pdf_magic(jina_result):
            self._log_fetch_event(
                logging.WARNING,
                "jina_pdf_payload",
                url,
                content_len=len(str(jina_result.get("content") or "")),
                required_len=required_length,
            )
            return {}
        if not has_sufficient_fetched_content(jina_result, required_length):
            self._log_fetch_event(
                logging.WARNING,
                "jina_content_short",
                url,
                content_len=len(str(jina_result.get("content") or "")),
                required_len=required_length,
            )
            return {}
        jina_result["fetch_method"] = "jina_reader"
        return jina_result

    def _apply_enrichment(self, doc_info: dict, evidence: WebPageEvidenceContent, fetched: dict) -> dict:
        """把压缩后的网页证据写回 doc_info。

        Args:
            doc_info: 原始 doc_info。
            evidence: 压缩后的证据正文和关键片段。
            fetched: 抓取结果。

        Returns:
            更新后的 doc_info 副本。
        """
        return apply_enrichment_to_doc(doc_info, evidence, fetched)

    async def _enrich_candidate(self, state: dict, loop_docs: list[dict], candidate_index: int) -> dict | None:
        """抓取并压缩单个候选网页。

        Args:
            state: 节点运行状态。
            loop_docs: 本轮新增文档列表副本。
            candidate_index: 候选列表中的 candidate_index。

        Returns:
            包含文档索引、原始定位键、增强文档和正文的结果；跳过或失败时返回 None。
        """
        candidates = state.get("candidates", [])
        if candidate_index < 0 or candidate_index >= len(candidates):
            return None
        candidate = candidates[candidate_index]
        doc_index = candidate["doc_index"]
        if doc_index < 0 or doc_index >= len(loop_docs):
            return None
        original_doc = loop_docs[doc_index]
        identity = capture_doc_identity(original_doc)
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        if not LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] fetching url. "
                "candidate_index=%s | doc_index=%s | url=%s | scores=%s",
                section_idx, step_title, candidate_index, doc_index, candidate.get("url", ""),
                candidate.get("scores", {}),
            )
        fetched = await self._fetch_webpage(
            candidate["url"],
            coerce_fetch_timeout_seconds(state.get("fetch_timeout_seconds")),
            len(str(original_doc.get("original_content") or "").strip()),
        )
        if not fetched:
            return None
        evidence = await self._compress_content(state, original_doc, fetched)
        if evidence is None:
            return None
        fetched_link_count = count_article_links(str(fetched.get("content") or ""))
        compressed_link_count = count_article_links(evidence.original_content)
        lost_link_count = max(0, fetched_link_count - compressed_link_count)
        if LogManager.is_sensitive():
            logger.info(
                "%s phase=link_preservation section_idx=%s "
                "fetched_content_link_count=%s compressed_content_link_count=%s "
                "lost_link_count=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                section_idx,
                fetched_link_count,
                compressed_link_count,
                lost_link_count,
            )
        else:
            logger.info(
                "%s phase=link_preservation section_idx=%s doc_id=%s url=%s "
                "fetched_content_link_count=%s compressed_content_link_count=%s "
                "lost_link_count=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                section_idx,
                original_doc.get("doc_id", ""),
                original_doc.get("url", ""),
                fetched_link_count,
                compressed_link_count,
                lost_link_count,
            )
        should_replace, quality_reason = should_replace_original_content(original_doc, evidence)
        if not should_replace:
            self._log_quality_rejection(
                QualityRejectionLogContext(
                    section_idx=section_idx,
                    step_title=step_title,
                    original_doc=original_doc,
                    quality_reason=quality_reason,
                    fetched=fetched,
                    evidence=evidence,
                )
            )
            return None
        enriched_doc = self._apply_enrichment(original_doc, evidence, fetched)
        article_link_source, sidecar_link_count = (
            build_article_link_source_with_count(str(fetched.get("content") or ""))
            if state.get("article_link_follow_enabled")
            else ("", 0)
        )
        if article_link_source:
            enriched_doc[ARTICLE_LINK_SOURCE_FIELD] = article_link_source
            enriched_doc[ARTICLE_LINK_SOURCE_COUNT_FIELD] = sidecar_link_count
        if state.get("article_link_follow_enabled"):
            logger.info(
                "%s phase=sidecar_build section_idx=%s "
                "fetched_content_link_count=%s sidecar_link_count=%s "
                "sidecar_length=%s sidecar_attached=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                section_idx,
                fetched_link_count,
                sidecar_link_count,
                len(article_link_source),
                str(bool(article_link_source)).lower(),
            )
        if not LogManager.is_sensitive():
            original_content = str(original_doc.get("original_content") or "")
            logger.debug(
                "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] original_content changed. "
                "doc_id=%s | url=%s | before_len=%s | after_len=%s",
                section_idx,
                step_title,
                original_doc.get("doc_id", ""),
                original_doc.get("url", ""),
                len(original_content),
                len(evidence.original_content),
            )
        self._log_enrichment_success(state, original_doc, enriched_doc, fetched, evidence)
        return {
            "doc_index": doc_index,
            "identity": identity,
            "doc": enriched_doc,
            "content": evidence.original_content,
        }

    async def _enrich_selected_candidates(self, state: dict, selected_indexes: list[int]) -> dict:
        """抓取并压缩 LLM 选中的候选网页。

        Args:
            state: 节点运行状态。
            selected_indexes: LLM 选择的 candidate_index 列表。

        Returns:
            更新后的状态片段。
        """
        loop_docs = list(state.get("new_doc_infos_current_loop") or [])
        all_docs = list(state.get("doc_infos") or [])
        source_store = dict(state.get("source_store") or {})
        replacements: list[tuple[dict[str, str], dict[str, Any]]] = []
        tasks = [
            self._enrich_candidate(state=state, loop_docs=loop_docs, candidate_index=candidate_index)
            for candidate_index in selected_indexes
        ]
        # fetch 与压缩并行执行；状态写回仍集中在 gather 之后，避免并发修改共享列表。
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, Exception):
                if LogManager.is_sensitive():
                    logger.warning(
                        "section_idx: %s | [WebPageEnrichmentNode] candidate enrichment failed. "
                        "category=candidate_enrichment_failed",
                        state.get("section_idx", 0),
                    )
                else:
                    logger.warning(
                        "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] "
                        "candidate enrichment failed. error=%s",
                        state.get("section_idx", 0),
                        state.get("step_title", ""),
                        result,
                        exc_info=(type(result), result, result.__traceback__),
                    )
                continue
            if result is None:
                continue
            enriched_doc = result["doc"]
            doc_index = result["doc_index"]
            loop_docs[doc_index] = enriched_doc
            all_index = find_matching_doc_index(all_docs, result["identity"])
            if all_index is not None:
                all_docs[all_index] = enriched_doc
            source_store[enriched_doc["source_id"]] = result["content"]
            replacements.append((result["identity"], enriched_doc))
        return {
            "new_doc_infos_current_loop": loop_docs,
            "doc_infos": all_docs,
            "history_queries": synchronize_history_queries(
                state.get("history_queries") or [],
                replacements,
            ),
            "source_store": source_store,
        }

    def _post_handle(self, updates: dict, session: Session) -> Output:
        """写回网页正文增强后的 collector 状态。

        Args:
            updates: `_enrich_selected_candidates` 返回的状态片段。
            session: 当前运行 session。

        Returns:
            空输出字典。
        """
        session.update_global_state({
            "collector_context.new_doc_infos_current_loop": updates["new_doc_infos_current_loop"]
        })
        session.update_global_state({"collector_context.doc_infos": updates["doc_infos"]})
        session.update_global_state({"collector_context.history_queries": updates["history_queries"]})
        session.update_global_state({"collector_context.source_store": updates["source_store"]})
        return {}

    async def _run_webpage_enrichment(self, state: dict, session: Session) -> dict | None:
        """执行已有网页正文增强阶段并在成功时写回状态。"""
        state["max_urls"] = state["webpage_enrichment_max_urls"]
        state["candidates"] = build_enrichment_candidates(
            state["new_doc_infos_current_loop"],
            limit=MAX_SELECTION_CANDIDATES,
        )
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [WebPageEnrichmentNode] built candidates. "
                "category=candidates_built | new_doc_count=%s | candidate_count=%s | max_urls=%s",
                state.get("section_idx", 0),
                len(state["new_doc_infos_current_loop"]),
                len(state["candidates"]),
                state.get("max_urls", 3),
            )
        else:
            logger.info(
                "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] built candidates. "
                "new_doc_count=%s | candidate_count=%s | max_urls=%s",
                state.get("section_idx", 0),
                state.get("step_title", ""),
                len(state["new_doc_infos_current_loop"]),
                len(state["candidates"]),
                state.get("max_urls", 3),
            )
        selected_indexes = await self._select_candidate_indexes(state)
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [WebPageEnrichmentNode] selected candidates. "
                "category=candidates_selected | selected_count=%s",
                state.get("section_idx", 0),
                len(selected_indexes),
            )
        else:
            logger.info(
                "section_idx: %s | step_title: %s | [WebPageEnrichmentNode] selected candidates. "
                "selected_count=%s | selected_indexes=%s",
                state.get("section_idx", 0),
                state.get("step_title", ""),
                len(selected_indexes),
                selected_indexes,
            )
        if not selected_indexes:
            return None
        updates = await self._enrich_selected_candidates(state, selected_indexes)
        self._post_handle(updates, session)
        return updates

    @staticmethod
    async def _safe_article_link_candidates(
        state: dict,
        stats: ArticleLinkFollowStats | None = None,
    ) -> list[ArticleLinkCandidate]:
        """构造链接候选并在线程中完成公网 DNS 校验。"""
        if stats is not None:
            stats.parent_doc_count = sum(
                1
                for doc in state["new_doc_infos_current_loop"]
                if isinstance(doc, dict) and str(doc.get("original_content") or "").strip()
            )
        existing_urls = {
            str(doc.get("url") or "")
            for doc in state["doc_infos"]
            if isinstance(doc, dict) and doc.get("url")
        }
        build_stats = ArticleLinkCandidateBuildStats()
        sidecar_sources = [
            str(doc.get(ARTICLE_LINK_SOURCE_FIELD) or "")
            for doc in state["new_doc_infos_current_loop"]
            if isinstance(doc, dict)
            and str(doc.get(ARTICLE_LINK_SOURCE_FIELD) or "").strip()
        ]
        sidecar_link_count = sum(
            int(doc.get(ARTICLE_LINK_SOURCE_COUNT_FIELD) or 0)
            for doc in state["new_doc_infos_current_loop"]
            if isinstance(doc, dict)
        )
        candidates = build_article_link_candidates(
            list(state["new_doc_infos_current_loop"]),
            existing_urls=existing_urls,
            attempted_urls=set(state["evidence_ledger"].attempted_links),
            stats=build_stats,
        )
        if stats is not None:
            stats.raw_candidate_count = len(candidates)
        logger.info(
            "%s phase=candidate_build section_idx=%s parent_doc_count=%s "
            "raw_candidate_count=%s sidecar_doc_count=%s sidecar_link_count=%s "
            "existing_url_count=%s attempted_url_count=%s",
            _ARTICLE_LINK_LOG_PREFIX,
            state["section_idx"],
            stats.parent_doc_count if stats is not None else "unknown",
            len(candidates),
            len(sidecar_sources),
            sidecar_link_count,
            len(existing_urls),
            len(state["evidence_ledger"].attempted_links),
        )
        logger.info(
            "%s phase=candidate_funnel section_idx=%s source_doc_count=%s "
            "depth_filtered_parent_count=%s empty_parent_count=%s "
            "unfollowable_parent_count=%s raw_extracted_link_count=%s "
            "invalid_url_count=%s blocked_suffix_count=%s "
            "self_link_filtered_count=%s existing_url_filtered_count=%s "
            "attempted_url_filtered_count=%s wikipedia_system_filtered_count=%s "
            "duplicate_link_count=%s "
            "parent_limit_filtered_count=%s total_limit_filtered_count=%s "
            "final_candidate_count=%s",
            _ARTICLE_LINK_LOG_PREFIX,
            state["section_idx"],
            build_stats.source_doc_count,
            build_stats.depth_filtered_parent_count,
            build_stats.empty_parent_count,
            build_stats.unfollowable_parent_count,
            build_stats.raw_extracted_link_count,
            build_stats.invalid_url_count,
            build_stats.blocked_suffix_count,
            build_stats.self_link_filtered_count,
            build_stats.existing_url_filtered_count,
            build_stats.attempted_url_filtered_count,
            build_stats.wikipedia_system_filtered_count,
            build_stats.duplicate_link_count,
            build_stats.parent_limit_filtered_count,
            build_stats.total_limit_filtered_count,
            build_stats.final_candidate_count,
        )

        async def validate(candidate: ArticleLinkCandidate) -> ArticleLinkCandidate | None:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(validate_public_web_url, candidate.url),
                    timeout=5,
                )
            except Exception as exc:
                if stats is not None:
                    stats.unsafe_count += 1
                logger.info(
                    "section_idx: %s | [WebPageEnrichmentNode] article link rejected. "
                    "category=article_link_unsafe error=%s",
                    state["section_idx"],
                    type(exc).__name__,
                )
                return None
            return candidate

        validated = await asyncio.gather(*(validate(candidate) for candidate in candidates))
        safe: list[ArticleLinkCandidate] = []
        for candidate in validated:
            if candidate is None:
                continue
            candidate.candidate_index = len(safe)
            safe.append(candidate)
        if stats is not None:
            stats.safe_candidate_count = len(safe)
        logger.info(
            "%s phase=safety_filter section_idx=%s raw_candidate_count=%s "
            "safe_candidate_count=%s unsafe_count=%s",
            _ARTICLE_LINK_LOG_PREFIX,
            state["section_idx"],
            len(candidates),
            len(safe),
            stats.unsafe_count if stats is not None else len(candidates) - len(safe),
        )
        return safe

    async def _compress_article_link_content(
        self,
        state: dict,
        candidate: ArticleLinkCandidate,
        reason: str,
        fetched: dict,
    ) -> ArticleLinkEvidence | None:
        """把 B 正文压缩为独立、有限的 collector 证据。"""
        fetched_content = str(fetched.get("content") or "")
        payload_content = fetched_content[:MAX_COLLECTOR_DOC_CONTENT_LENGTH * 10]
        final_url = str(fetched.get("url") or candidate.url)

        def log_diagnostic(
            outcome: str,
            *,
            empty_reason: str = "none",
            compressed_content_length: int = 0,
            key_passage_count: int = 0,
        ) -> None:
            logger.info(
                "%s phase=compression_diagnostic section_idx=%s outcome=%s "
                "fetched_content_length=%s payload_content_length=%s "
                "fetch_method=%s redirected=%s title_present=%s "
                "compressed_content_length=%s key_passage_count=%s "
                "empty_reason=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                state["section_idx"],
                outcome,
                len(fetched_content),
                len(payload_content),
                str(fetched.get("fetch_method") or "unknown"),
                str(final_url != candidate.url).lower(),
                str(bool(str(fetched.get("title") or "").strip())).lower(),
                compressed_content_length,
                key_passage_count,
                empty_reason,
            )

        log_diagnostic("start")
        payload = {
            "task": {
                "step_title": state["step_title"],
                "step_description": state["step_description"],
            },
            "url": fetched.get("url") or candidate.url,
            "title": fetched.get("title") or "",
            "anchor_text": candidate.anchor_text,
            "selection_reason": reason,
            "webpage_content": payload_content,
        }
        evidence = await self._invoke_structured_llm(
            state=state,
            prompt_name="collector_article_link_follow_compress",
            payload=payload,
            agent_name=AgentLlmName.COLLECTOR_ARTICLE_LINK_FOLLOW_COMPRESSION.value,
            schema=ArticleLinkEvidence,
            operation="compress article link content",
        )
        if evidence is None:
            log_diagnostic("empty", empty_reason="structured_result_none")
            return None
        if not evidence.original_content.strip():
            log_diagnostic(
                "empty",
                empty_reason="empty_original_content",
                key_passage_count=len(evidence.key_passages),
            )
            return None
        evidence.original_content = evidence.original_content[:MAX_COLLECTOR_DOC_CONTENT_LENGTH]
        log_diagnostic(
            "success",
            compressed_content_length=len(evidence.original_content),
            key_passage_count=len(evidence.key_passages),
        )
        return evidence

    async def _follow_article_candidate(
        self,
        state: dict,
        candidate: ArticleLinkCandidate,
        reason: str,
        stats: ArticleLinkFollowStats | None = None,
    ) -> FollowedArticle | None:
        """复用网页抓取管线抓取 B，并构造独立证据文档。"""
        timeout_seconds = coerce_fetch_timeout_seconds(state["fetch_timeout_seconds"])
        self._log_article_link_item(
            section_idx=state["section_idx"],
            stage="fetch",
            outcome="start",
            candidate=candidate,
            reason=reason,
        )
        try:
            fetched = await self._fetch_webpage(candidate.url, timeout_seconds)
        except Exception as exc:
            self._log_article_link_item(
                section_idx=state["section_idx"], stage="fetch", outcome="failed",
                candidate=candidate, reason=reason, error=exc,
            )
            raise
        if not fetched:
            self._log_article_link_item(
                section_idx=state["section_idx"], stage="fetch", outcome="empty",
                candidate=candidate, reason=reason,
            )
            return None
        if stats is not None:
            stats.fetch_success_count += 1
        self._log_article_link_item(
            section_idx=state["section_idx"], stage="fetch", outcome="success",
            candidate=candidate, reason=reason,
        )
        final_url = str(fetched.get("url") or candidate.url)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(validate_public_web_url, final_url),
                timeout=5,
            )
        except Exception as exc:
            self._log_article_link_item(
                section_idx=state["section_idx"], stage="redirect_validation",
                outcome="failed", candidate=candidate, reason=reason, error=exc,
            )
            raise
        self._log_article_link_item(
            section_idx=state["section_idx"], stage="redirect_validation",
            outcome="success", candidate=candidate, reason=reason,
        )
        evidence = await self._compress_article_link_content(
            state,
            candidate,
            reason,
            fetched,
        )
        if evidence is None:
            self._log_article_link_item(
                section_idx=state["section_idx"], stage="compression", outcome="empty",
                candidate=candidate, reason=reason,
            )
            return None
        if stats is not None:
            stats.compression_success_count += 1
        self._log_article_link_item(
            section_idx=state["section_idx"], stage="compression", outcome="success",
            candidate=candidate, reason=reason,
        )

        temporary_store = CollectorSourceStore()
        _, doc_info = build_evidence_atom(
            record={
                "url": final_url,
                "title": evidence.title or fetched.get("title") or candidate.anchor_text or "Untitled",
                "content": evidence.original_content,
                "type": "web",
            },
            query=candidate.query or state["step_title"],
            source_store=temporary_store,
        )
        if evidence.key_passages:
            doc_info["key_passages"] = evidence.key_passages
        scored = await run_doc_evaluation(
            query=doc_info["query"],
            documents=build_evaluation_documents([doc_info]),
            llm=self.llm,
        )
        if not scored:
            self._log_article_link_item(
                section_idx=state["section_idx"], stage="evaluation", outcome="empty",
                candidate=candidate, reason=reason,
            )
            return None
        if stats is not None:
            stats.evaluation_success_count += 1
        self._log_article_link_item(
            section_idx=state["section_idx"], stage="evaluation", outcome="success",
            candidate=candidate, reason=reason,
        )
        doc_info["scores"] = normalize_scores(scored[0].get("scores"))
        doc_info["publish_time"] = (
            scored[0].get("publish_time")
            or scored[0].get("doc_time")
            or doc_info.get("publish_time")
        )
        normalize_doc_info_scores_and_time(doc_info)
        doc_info["discovery"] = {
            "method": "article_link_follow",
            "depth": 1,
            "parent_doc_id": candidate.parent_doc_id,
            "parent_url": candidate.parent_url,
            "anchor_text": candidate.anchor_text,
            "selection_reason": reason,
            "discovered_from": [asdict(origin) for origin in candidate.origins],
        }
        return FollowedArticle(
            candidate_index=candidate.candidate_index,
            canonical_url=candidate.canonical_url,
            doc_info=doc_info,
            source_content=(
                temporary_store.read(doc_info["source_id"]) or evidence.original_content
            ),
        )

    @staticmethod
    def _append_article_to_parent_history(history_queries: list[Any], doc_info: dict) -> None:
        """把 B 附加到对应父 query 的历史文档列表。"""
        query = str(doc_info.get("query") or "")
        for retrieval_query in history_queries:
            if getattr(retrieval_query, "query", "") != query:
                continue
            existing = getattr(retrieval_query, "doc_infos", None)
            if existing is None:
                retrieval_query.doc_infos = []
                existing = retrieval_query.doc_infos
            if not any(item.get("source_id") == doc_info.get("source_id") for item in existing):
                existing.append(doc_info)
            return

    @classmethod
    def _write_article_link_results(
        cls,
        session: Session,
        state: dict,
        selected: list[tuple[int, str]],
        candidates: list[ArticleLinkCandidate],
        results: list[FollowedArticle | BaseException | None],
    ) -> ArticleLinkWritebackStats:
        """集中写回成功 B 和链接尝试 ledger。"""
        attempted = [candidates[index].canonical_url for index, _ in selected]
        followed = [result for result in results if isinstance(result, FollowedArticle)]
        successful: list[str] = []
        docs = list(state["doc_infos"])
        loop_docs = list(state["new_doc_infos_current_loop"])
        history = copy.deepcopy(state["history_queries"])
        source_store = dict(state["source_store"])
        existing_urls = {
            canonicalize_url(str(doc.get("url") or ""))
            for doc in docs
            if isinstance(doc, dict)
        }
        duplicate_count = 0
        for result in followed:
            doc_url = canonicalize_url(str(result.doc_info.get("url") or ""))
            if doc_url in existing_urls:
                duplicate_count += 1
                continue
            existing_urls.add(doc_url)
            docs.append(result.doc_info)
            loop_docs.append(result.doc_info)
            source_store.setdefault(result.doc_info["source_id"], result.source_content)
            cls._append_article_to_parent_history(history, result.doc_info)
            successful.append(result.canonical_url)
        failed = [url for url in attempted if url not in set(successful)]
        ledger = append_link_attempts(
            state["evidence_ledger"],
            attempted=attempted,
            successful=successful,
            failed=failed,
        )
        state.update({
            "doc_infos": docs,
            "new_doc_infos_current_loop": loop_docs,
            "history_queries": history,
            "source_store": source_store,
            "evidence_ledger": ledger,
        })
        session.update_global_state({
            "collector_context.doc_infos": docs,
            "collector_context.new_doc_infos_current_loop": loop_docs,
            "collector_context.history_queries": history,
            "collector_context.source_store": source_store,
            "collector_context.evidence_ledger": ledger.model_dump(),
        })
        return ArticleLinkWritebackStats(
            attempted_count=len(attempted),
            successful_count=len(successful),
            failed_count=len(failed),
            duplicate_count=duplicate_count,
        )

    async def _run_article_link_follow(self, state: dict, session: Session) -> None:
        """选择、抓取并集中写回本轮一跳 B 文档。"""
        stats = ArticleLinkFollowStats()
        started = time.perf_counter()
        logger.info(
            "%s phase=start section_idx=%s enabled=true max_urls=%s",
            _ARTICLE_LINK_LOG_PREFIX,
            state["section_idx"],
            state["article_link_follow_max_urls"],
        )
        try:
            candidates = await self._safe_article_link_candidates(state, stats)
            if not candidates:
                return
            ranked = select_article_link_candidates(
                candidates,
                task_text=" ".join((
                    state["plan_title"],
                    state["plan_thought"],
                    state["step_title"],
                    state["step_description"],
                )),
                max_urls=state["article_link_follow_max_urls"],
            )
            selected = [
                (item.candidate_index, ",".join(item.reasons))
                for item in ranked
            ]
            stats.selected_count = len(selected)
            logger.info(
                "%s phase=selection section_idx=%s safe_candidate_count=%s "
                "selected_count=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                state["section_idx"],
                len(candidates),
                len(selected),
            )
            if not LogManager.is_sensitive():
                for index, reason in selected:
                    candidate = candidates[index]
                    logger.info(
                        "%s phase=selection_item section_idx=%s candidate_index=%s "
                        "url=%s parent_url=%s anchor_text=%s reasons=%s",
                        _ARTICLE_LINK_LOG_PREFIX,
                        state["section_idx"],
                        index,
                        candidate.url,
                        candidate.parent_url,
                        candidate.anchor_text,
                        reason,
                    )
            if not selected:
                return
            results = await asyncio.gather(*(
                self._follow_article_candidate(
                    state, candidates[index], reason, stats
                )
                for index, reason in selected
            ), return_exceptions=True)
            writeback = self._write_article_link_results(
                session, state, selected, candidates, results
            )
            stats.writeback_count = writeback.successful_count
            stats.failed_count = writeback.failed_count
            stats.duplicate_count = writeback.duplicate_count
            logger.info(
                "%s phase=writeback section_idx=%s attempted_count=%s "
                "successful_count=%s failed_count=%s duplicate_count=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                state["section_idx"],
                writeback.attempted_count,
                writeback.successful_count,
                writeback.failed_count,
                writeback.duplicate_count,
            )
        finally:
            self._cleanup_article_link_sources(state, session)
            logger.info(
                "%s phase=summary section_idx=%s parent_doc_count=%s "
                "raw_candidate_count=%s safe_candidate_count=%s unsafe_count=%s "
                "selected_count=%s fetch_success_count=%s "
                "compression_success_count=%s evaluation_success_count=%s "
                "writeback_count=%s failed_count=%s duplicate_count=%s duration_ms=%s",
                _ARTICLE_LINK_LOG_PREFIX,
                state["section_idx"],
                stats.parent_doc_count,
                stats.raw_candidate_count,
                stats.safe_candidate_count,
                stats.unsafe_count,
                stats.selected_count,
                stats.fetch_success_count,
                stats.compression_success_count,
                stats.evaluation_success_count,
                stats.writeback_count,
                stats.failed_count,
                stats.duplicate_count,
                round((time.perf_counter() - started) * 1000),
            )

    @staticmethod
    def _cleanup_article_link_sources(state: dict, session: Session) -> None:
        """Remove the temporary pre-compression link source from collector state."""
        def clean_docs(docs: list) -> tuple[list, bool]:
            cleaned: list = []
            changed = False
            for doc in docs:
                if isinstance(doc, dict) and (
                    ARTICLE_LINK_SOURCE_FIELD in doc
                    or ARTICLE_LINK_SOURCE_COUNT_FIELD in doc
                ):
                    updated = dict(doc)
                    updated.pop(ARTICLE_LINK_SOURCE_FIELD, None)
                    updated.pop(ARTICLE_LINK_SOURCE_COUNT_FIELD, None)
                    cleaned.append(updated)
                    changed = True
                else:
                    cleaned.append(doc)
            return cleaned, changed

        def clean_history(history_queries: list) -> tuple[list, bool]:
            cleaned: list = []
            changed = False
            for query in history_queries:
                if isinstance(query, dict):
                    docs, docs_changed = clean_docs(query.get("doc_infos") or [])
                    if docs_changed:
                        updated = dict(query)
                        updated["doc_infos"] = docs
                        cleaned.append(updated)
                        changed = True
                    else:
                        cleaned.append(query)
                    continue
                docs, docs_changed = clean_docs(
                    getattr(query, "doc_infos", None) or []
                )
                if docs_changed:
                    cleaned.append(query.model_copy(update={"doc_infos": docs}))
                    changed = True
                else:
                    cleaned.append(query)
            return cleaned, changed

        payload: dict[str, list] = {}
        for state_key in ("new_doc_infos_current_loop", "doc_infos"):
            global_key = f"collector_context.{state_key}"
            current = state.get(state_key)
            if not isinstance(current, list):
                current = session.get_global_state(global_key) or []
            cleaned, changed = clean_docs(current)
            if changed:
                state[state_key] = cleaned
                payload[global_key] = cleaned

        history = state.get("history_queries")
        if not isinstance(history, list):
            history = session.get_global_state(
                "collector_context.history_queries"
            ) or []
        cleaned_history, history_changed = clean_history(history)
        if history_changed:
            state["history_queries"] = cleaned_history
            payload["collector_context.history_queries"] = cleaned_history
        if payload:
            session.update_global_state(payload)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """按独立开关依次执行网页增强和文章内链接跟进。

        Args:
            inputs: 当前节点输入。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            空输出字典，固定进入后续 Supervisor。
        """
        state = self._pre_handle(inputs, session, context)
        session_context.set(session)
        if not (
            state["webpage_enrichment_enabled"]
            or state["article_link_follow_enabled"]
        ):
            logger.info("[WebPageEnrichmentNode] all webpage phases disabled, skip.")
            return {}
        if state["webpage_enrichment_enabled"]:
            updates = await self._run_webpage_enrichment(state, session)
            if updates:
                state.update(updates)
        if state["article_link_follow_enabled"]:
            await self._run_article_link_follow(state, session)
        return {}
