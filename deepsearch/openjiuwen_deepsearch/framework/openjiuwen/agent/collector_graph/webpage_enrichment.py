# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import extract_key_passages
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
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
    WebFetchWebpageAdapter,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.registry import (
    resolve_web_fetch_provider,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina import (
    JinaWebFetchProvider,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, record_llm_retry_log
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context, session_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)
MAX_SELECTION_CANDIDATES = 10
# enrichment 抓取并发上限: 避免 JinaWebFetchProvider 瞬时高并发打爆镜像
ENRICH_FETCH_CONCURRENCY = 3
# httpx 直连路本地解析 PDF 的最大页数
SIMPLE_UA_PDF_MAX_PAGES = 50
# httpx 直连路下载阶段的总时长上限。httpx 自带的 timeout 只作用于单次操作,
# 慢速滴流和连接重试会累加, 所以要给整个下载阶段单独设界, 也给后面的解析和 jina 兜底留出预算。
SIMPLE_UA_DOWNLOAD_TIMEOUT_SECONDS = 30
# httpx 直连路解析阶段的时长上限。下载上限 + 解析上限把单 URL 预算切成两段, 各自有界。
SIMPLE_UA_PARSE_TIMEOUT_SECONDS = 15
# httpx 直连路下载字节上限。只作 OOM 护栏, 取值远高于真实 PDF:
# 按字节截断会破坏 PDF 尾部的交叉引用表, 导致本可解析的文档解析失败。
SIMPLE_UA_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
# Jina Reader 路的阶段上限。provider 内部是固定 3 次重试 + 线性退避(最坏约 37s),
# 自身不知道还剩多少预算, 这里给它一个独立上限: 与 A/B 路一样"每段各自有界"。
# 注意 _remaining_timeout_seconds 向上取整, 阶段上限因而恒 >= 实际剩余: 只有剩余
# 超过 30s 时才由它先到并把原因记成 jina_fetch_failed, 否则先到的是外层 deadline
# (记 fetch_deadline_exceeded)。
# 同一个值也作为 budget 传给 provider: asyncio.to_thread 的线程不可取消, 但内部
# 重试、退避与单次请求超时都被压进这个预算, 线程收在预算附近(最坏再多花一次请求的
# connect + read), 不会跑满固定的 3 次重试。预算充裕时不触发, 与设界前一致。
JINA_STAGE_TIMEOUT_SECONDS = 30


def _remaining_timeout_seconds(deadline: float) -> int:
    """计算整体抓取 deadline 剩余的请求超时秒数。

    Args:
        deadline: event loop 单调时钟上的绝对截止时间。

    Returns:
        传给同步 HTTP 请求的正整数秒数；整体上限由外层 asyncio deadline 保证。
    """
    remaining = deadline - asyncio.get_running_loop().time()
    return max(1, math.ceil(remaining))


def _elapsed_ms(started: float) -> int:
    """返回自 ``started`` 起经过的毫秒数，用于抓取各阶段耗时日志。"""
    return int((time.monotonic() - started) * 1000)


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


class WebPageEnrichmentNode(BaseNode):
    """选择性抓取并压缩本轮网页搜索结果。"""

    def __init__(self):
        """初始化网页正文增强节点。"""
        super().__init__()
        self.llm: Any = None
        # jina 兜底 provider；_pre_handle 会在 enabled 时覆盖它。
        # 这里给默认值，使不经 _pre_handle 的直接调用（测试等）落回 legacy 分支而非 AttributeError。
        self._jina_provider: Any = None

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
        enabled = bool(session.get_global_state("config.info_collector_webpage_enrich_enable"))
        self.llm = None
        # jina 兜底用 JinaWebFetchProvider(带 Bearer / 多 base / 鉴权失败不短路)。
        # 配了 web_fetch_provider_config(provider=jina) 就用配置; 否则默认构造(无 key, 走 r.jinaai.cn 镜像)。
        self._jina_provider = None
        if enabled:
            llm_model_name = adapt_llm_model_name(session, NodeId.INFO_COLLECTOR.value)
            self.llm = llm_context.get().get(llm_model_name)
            _wfpc = session.get_global_state("config.web_fetch_provider_config") or {}
            _provider_name, _provider = resolve_web_fetch_provider(_wfpc)
            if _provider_name == "jina" and _provider is not None:
                self._jina_provider = _provider
            else:
                self._jina_provider = JinaWebFetchProvider()
        return {
            "enabled": enabled,
            "max_urls": session.get_global_state("config.info_collector_webpage_enrich_max_urls") or 3,
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
        formatted_prompt = [
            *apply_system_prompt("collector_webpage_enrichment_select", {}),
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        try:
            decision = await ainvoke_llm_with_stats(
                self.llm,
                formatted_prompt,
                agent_name=AgentLlmName.COLLECTOR_WEBPAGE_ENRICHMENT_SELECTION.value,
                schema=WebPageEnrichmentDecision,
            )
        except Exception as exc:
            record_llm_retry_log(
                current_try=1,
                max_retries=1,
                section_idx=state.get("section_idx", 0),
                step_title=state.get("step_title", ""),
                error=exc,
                operation="select webpage enrichment candidates",
            )
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
        formatted_prompt = [
            *apply_system_prompt("collector_webpage_enrichment_compress", {}),
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        try:
            evidence = await ainvoke_llm_with_stats(
                self.llm,
                formatted_prompt,
                agent_name=AgentLlmName.COLLECTOR_WEBPAGE_ENRICHMENT_COMPRESSION.value,
                schema=WebPageEvidenceContent,
            )
        except Exception as exc:
            record_llm_retry_log(
                current_try=1,
                max_retries=1,
                section_idx=state.get("section_idx", 0),
                step_title=state.get("step_title", ""),
                error=exc,
                operation="compress fetched webpage content",
            )
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
        extra: dict[str, Any] | None = None,
    ) -> None:
        """记录抓取事件，并在敏感模式下移除 URL 和异常正文。

        Args:
            level: Python logging 级别。
            category: 固定抓取事件分类。
            url: 非敏感模式下用于定位的目标 URL。
            content_len: 抓取正文长度。
            required_len: 当前动态最低正文长度。
            exc: 抓取异常；仅非敏感模式记录。
            extra: 额外的非敏感键值对（如阶段耗时、下载字节数），按 ``key=value`` 追加。

        Returns:
            None.
        """
        extra_text = "".join(f" {key}={value}" for key, value in extra.items()) if extra else ""
        if LogManager.is_sensitive():
            logger.log(
                level,
                "[WebPageEnrichmentNode] fetch event. category=%s content_len=%s required_len=%s%s",
                category,
                content_len,
                required_len,
                extra_text,
            )
            return
        logger.log(
            level,
            "[WebPageEnrichmentNode] fetch event. category=%s url=%s content_len=%s "
            "required_len=%s%s error=%s",
            category,
            url,
            content_len,
            required_len,
            extra_text,
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
            "raw_len=%s | compressed_len=%s | key_passages=%s",
            state.get("section_idx", 0),
            state.get("step_title", ""),
            enriched_doc.get("doc_id", ""),
            enriched_doc.get("source_id", ""),
            fetched.get("status_code", ""),
            original_len,
            raw_len,
            compressed_len,
            key_passage_count,
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

    async def _fetch_via_simple_ua(
        self,
        url: str,
        deadline: float,
        required_length: int,
    ) -> dict:
        """httpx 直连 + HTML/PDF 抽取。

        补 harness 直连没有的 PDF 抓取能力: 它对 `.pdf` 结尾的 URL 直接跳过, 对其它 URL
        拿到的 PDF 字节也不解析、直接弃用; 本方法保留 httpx 返回的原始 bytes 并交
        extract_pdf 处理。

        同时换了一个请求客户端(本方法 httpx, harness 直连是 aiohttp), 覆盖到 harness
        直连取不到正文的站点。

        下载与解析各自设时长上限(`SIMPLE_UA_DOWNLOAD_TIMEOUT_SECONDS` /
        `SIMPLE_UA_PARSE_TIMEOUT_SECONDS`), 下载另有字节上限
        (`SIMPLE_UA_MAX_DOWNLOAD_BYTES`); 三者都从当前 URL 的总 deadline 里取,
        避免某个阶段无限期占用预算, 也给后面的 jina 兜底留出重试时间。

        Args:
            url: 目标 URL。
            deadline: event loop 单调时钟上的整体截止时间。
            required_length: 抓取正文动态最低长度。

        Returns:
            成功返回结构化结果(含 ``fetch_method='simple_ua'``), 失败返回空字典。
        """
        import httpx
        from openjiuwen.harness.tools.web import WebFetchWebpageTool
        from openjiuwen.harness.tools.web._decode import _decode_response_text
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.full_text import (
            extract_pdf,
        )

        remaining = _remaining_timeout_seconds(deadline)
        download_timeout = max(1, min(SIMPLE_UA_DOWNLOAD_TIMEOUT_SECONDS, remaining))
        download_started = time.monotonic()
        downloaded_bytes = 0
        status_code = 0
        response_headers: dict[str, str] = {}
        data = b""
        try:
            async with httpx.AsyncClient(
                timeout=download_timeout,
                follow_redirects=True,
                trust_env=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DeepResearchEnrichment/1.0)"},
            ) as client:
                async with asyncio.timeout(download_timeout):
                    async with client.stream("GET", url) as resp:
                        status_code = resp.status_code
                        response_headers = resp.headers
                        chunks: list[bytes] = []
                        if status_code == 200:
                            async for chunk in resp.aiter_bytes():
                                downloaded_bytes += len(chunk)
                                if downloaded_bytes > SIMPLE_UA_MAX_DOWNLOAD_BYTES:
                                    raise ValueError(
                                        "download size exceeds "
                                        f"{SIMPLE_UA_MAX_DOWNLOAD_BYTES} bytes"
                                    )
                                chunks.append(chunk)
                        data = b"".join(chunks)
        except Exception as exc:
            self._log_fetch_event(
                logging.WARNING,
                "simple_ua_failed",
                url,
                required_len=required_length,
                exc=exc,
                extra={
                    "status": status_code,
                    "downloaded_bytes": downloaded_bytes,
                    "download_ms": _elapsed_ms(download_started),
                },
            )
            return {}
        if status_code != 200 or not data:
            self._log_fetch_event(
                logging.INFO,
                "simple_ua_failed",
                url,
                content_len=len(data),
                required_len=required_length,
                extra={"status": status_code, "download_ms": _elapsed_ms(download_started)},
            )
            return {}
        content_type = response_headers.get("Content-Type", "")
        download_ms = _elapsed_ms(download_started)
        title = ""
        truncated = False
        parse_timeout = max(1, min(SIMPLE_UA_PARSE_TIMEOUT_SECONDS, _remaining_timeout_seconds(deadline)))
        parse_started = time.monotonic()
        try:
            if "pdf" in content_type.lower() or data[:5] == b"%PDF-":
                text, truncated = await asyncio.wait_for(
                    asyncio.to_thread(
                        extract_pdf,
                        data,
                        MAX_COLLECTOR_DOC_CONTENT_LENGTH,
                        SIMPLE_UA_PDF_MAX_PAGES,
                    ),
                    timeout=parse_timeout,
                )
            else:
                # 流式读取后 httpx 不再保留 resp.text; 改用 harness 的解码函数, 与 A 路一致:
                # 先按 Content-Type 声明的字符集, 缺失时交给 charset_normalizer 嗅探。
                # 直接用 httpx 的 .text 在缺 charset 的 GBK 等页面上会整篇解码成乱码。
                decoded = _decode_response_text(data, content_type=content_type)
                if "html" in content_type.lower():
                    title, decoded = WebFetchWebpageTool._extract_main_text_from_html(decoded)
                text = decoded[:MAX_COLLECTOR_DOC_CONTENT_LENGTH]
                truncated = len(decoded) > MAX_COLLECTOR_DOC_CONTENT_LENGTH
        except Exception as exc:
            self._log_fetch_event(
                logging.WARNING,
                "simple_ua_parse_failed",
                url,
                content_len=len(data),
                required_len=required_length,
                exc=exc,
                extra={
                    "downloaded_bytes": downloaded_bytes,
                    "download_ms": download_ms,
                    "parse_ms": _elapsed_ms(parse_started),
                },
            )
            return {}
        parse_ms = _elapsed_ms(parse_started)
        if not text or len(text) < required_length:
            self._log_fetch_event(
                logging.INFO,
                "simple_ua_short",
                url,
                content_len=len(text),
                required_len=required_length,
                extra={"download_ms": download_ms, "parse_ms": parse_ms},
            )
            return {}
        self._log_fetch_event(
            logging.INFO,
            "simple_ua_ok",
            url,
            content_len=len(text),
            required_len=required_length,
            extra={
                "downloaded_bytes": downloaded_bytes,
                "download_ms": download_ms,
                "parse_ms": parse_ms,
            },
        )
        return {
            "url": url,
            "status_code": 200,
            "title": title,
            "content": text,
            "truncated": truncated,
            "fetch_method": "simple_ua",
        }

    async def _fetch_webpage_before_deadline(
        self,
        url: str,
        deadline: float,
        required_length: int,
    ) -> dict:
        """依次尝试三条抓取路, 前一条拿不到正文才退到下一条:
        harness 直连(完整 Chrome UA) -> httpx 直连(带 PDF 本地解析) -> jina reader 代理。

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
        # harness 直连失败或正文太短时, 换 httpx 直连再试一次
        b_result = await self._fetch_via_simple_ua(url, deadline, required_length)
        if b_result:
            return b_result
        # jina 兜底: 用带鉴权/多 base 的 JinaWebFetchProvider。
        # 生产路径下 _pre_handle 必会构造 provider，故此处恒为真; `is not None` 保留给直接调用的 legacy 回退。
        if self._jina_provider is not None:
            try:
                # 给 C 路一个独立阶段上限, 与 A/B 路一致; 取 min 保证不超过剩余总预算。
                # 同一个值再作为预算传进 provider: to_thread 的线程不可取消, 只能让它
                # 把内部重试、退避与单次请求超时都压进这个窗口, 到点自行结束。
                stage_timeout = float(
                    max(1, min(JINA_STAGE_TIMEOUT_SECONDS, _remaining_timeout_seconds(deadline)))
                )
                jina_content = await asyncio.wait_for(
                    asyncio.to_thread(self._jina_provider.fetch_page, url, budget=stage_timeout),
                    timeout=stage_timeout,
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
            if not jina_content or jina_content.startswith("[web_fetch] Failed"):
                self._log_fetch_event(
                    logging.WARNING,
                    "jina_fetch_failed",
                    url,
                    content_len=len(jina_content or ""),
                    required_len=required_length,
                )
                return {}
            if has_pdf_magic({"content": jina_content}):
                self._log_fetch_event(
                    logging.WARNING,
                    "jina_pdf_payload",
                    url,
                    content_len=len(jina_content),
                    required_len=required_length,
                )
                return {}
            if len(jina_content) < required_length:
                self._log_fetch_event(
                    logging.WARNING,
                    "jina_content_short",
                    url,
                    content_len=len(jina_content),
                    required_len=required_length,
                )
                return {}
            self._log_fetch_event(
                logging.INFO,
                "jina_provider_ok",
                url,
                content_len=len(jina_content),
                required_len=required_length,
            )
            return {
                "url": url,
                "status_code": 200,
                "title": "",
                "content": jina_content,
                "truncated": False,
                "fetch_method": "jina_provider",
            }
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
                "candidate_index=%s | doc_index=%s | url=%s",
                section_idx, step_title, candidate_index, doc_index, candidate.get("url", ""),
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
        # 限并发, 避免瞬时高并发打爆镜像
        sem = asyncio.Semaphore(ENRICH_FETCH_CONCURRENCY)

        async def _bounded_enrich(candidate_index: int):
            async with sem:
                return await self._enrich_candidate(
                    state=state, loop_docs=loop_docs, candidate_index=candidate_index
                )

        bounded_tasks = [_bounded_enrich(i) for i in selected_indexes]
        # fetch 与压缩并行执行；状态写回仍集中在 gather 之后，避免并发修改共享列表。
        for result in await asyncio.gather(*bounded_tasks, return_exceptions=True):
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

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行网页正文增强节点。

        Args:
            inputs: 当前节点输入。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            空输出字典，固定进入后续 Supervisor。
        """
        state = self._pre_handle(inputs, session, context)
        session_context.set(session)
        if not state["enabled"]:
            logger.info("[WebPageEnrichmentNode] disabled, skip webpage enrichment.")
            return {}
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
            return {}
        updates = await self._enrich_selected_candidates(state, selected_indexes)
        return self._post_handle(updates, session)
