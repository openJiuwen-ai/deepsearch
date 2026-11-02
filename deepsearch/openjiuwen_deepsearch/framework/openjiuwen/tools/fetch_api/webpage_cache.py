# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""首次章节采集执行范围内的网页正文缓存与并发抓取合并。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

from openjiuwen_deepsearch.algorithm.research_collector.enrichment_content import prepare_fetched_content

from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import (
    has_pdf_magic,
    has_sufficient_fetched_content,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)
FetchCallback = Callable[[float, int], Awaitable[dict[str, Any]]]


def log_fetch_cache_event(category: str, url: str | None = None, **metrics: Any) -> None:
    """记录缓存与抓取的运行事件，敏感模式不包含 URL。

    Args:
        category: 固定事件类别。
        url: 可选的原始 URL。
        metrics: 不含正文或凭据的计数与长度指标。
    """
    if url is not None:
        if LogManager.is_sensitive():
            metrics["url_redacted"] = True
        else:
            metrics["url"] = url
    logger.info(
        "[WebPageEnrichmentNode] category=%s metrics_json=%s",
        category,
        json.dumps(metrics, ensure_ascii=True),
        extra={"skip_truncation": True},
    )


def is_cacheable_fetch(fetched: dict[str, Any], required_length: int) -> bool:
    """检查正文是否有效、满足现有长度门槛且不是未解析 PDF。

    Args:
        fetched: 原抓取流程返回的结果。
        required_length: 本次正文最低长度。

    Returns:
        可以缓存和复用时返回 True。
    """
    prepared, reason = prepare_fetched_content(fetched)
    return not reason and has_sufficient_fetched_content(prepared, required_length) and not has_pdf_magic(prepared)


class TaskWebpageFetchCache:
    """在同一事件循环的采集执行范围中共享正文，不设 TTL 或容量限制。

    Attributes:
        closed: 是否已经关闭；关闭后不再接受结果。
    """

    def __init__(self) -> None:
        """初始化正文、在途任务和占用统计。"""
        self._results: dict[str, dict[str, Any]] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._counts: Counter[str] = Counter()
        self._bytes = 0
        self._peak_bytes = 0
        self.closed = False

    def snapshot(self) -> dict[str, int]:
        """返回计数和正文字符串占用估算，不代表进程 RSS。

        Returns:
            当前条目、正文估算字节数、峰值与抓取计数。
        """
        return {
            **self._counts,
            "cache_entries": len(self._results),
            "cache_bytes": self._bytes,
            "cache_peak_bytes": self._peak_bytes,
        }

    async def get_or_fetch(
        self, url: str, deadline: float, required_length: int, fetcher: FetchCallback,
    ) -> dict[str, Any]:
        """复用正文或等待同 URL 抓取，长度不足时使用剩余预算补抓。

        Args:
            url: 精确匹配的原始 URL。
            deadline: 当前调用者的单调时钟截止时间，包含等待耗时。
            required_length: 当前调用者的正文最低长度。
            fetcher: 接收截止时间和长度门槛的原抓取回调。

        Returns:
            独立结果副本；抓取失败或缓存关闭时返回空字典。

        Raises:
            TimeoutError: 当前调用者等待超过截止时间。
            Exception: 原抓取回调抛出的非超时异常。
        """
        self._counts["cache_requests"] += 1
        async with asyncio.timeout_at(deadline):
            while not self.closed:
                cached = self._results.get(url)
                if cached is not None:
                    # 内部结果仅在完整清理和验证后存入，复用只检查调用者长度门槛。
                    if has_sufficient_fetched_content(cached, required_length):
                        self._counts["cache_hit"] += 1
                        log_fetch_cache_event("enrichment_cache_hit", url)
                        return copy.deepcopy(cached)
                    self._counts["cache_insufficient"] += 1
                    log_fetch_cache_event("enrichment_cache_insufficient", url, required_len=required_length)

                flight = self._inflight.get(url)
                if flight is None:
                    # 查找与注册之间不 await，保证同一事件循环内只有一个生产者。
                    flight = asyncio.create_task(self._fetch_and_store(url, deadline, required_length, fetcher))
                    self._inflight[url] = flight
                    # 所有等待者离开后，仍须消费迟到异常，避免未处理的 Task 异常。
                    flight.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                else:
                    self._counts["cache_wait"] += 1
                    log_fetch_cache_event("enrichment_cache_wait", url)

                # 调用者超时或取消只停止自己的等待；任务关闭负责取消生产者。
                fetched = await asyncio.shield(flight)
                if self.closed or not fetched:
                    return {}
                if has_sufficient_fetched_content(fetched, required_length):
                    return copy.deepcopy(fetched)
                # 共享任务可能按较低门槛提前返回；下一次循环以本次门槛补抓。
            return {}

    async def _fetch_and_store(
        self, url: str, deadline: float, required_length: int, fetcher: FetchCallback,
    ) -> dict[str, Any]:
        """执行有界共享抓取并在成功后保存正文。

        Args:
            url: 原始 URL。
            deadline: 发起者的抓取截止时间。
            required_length: 发起者的正文最低长度。
            fetcher: 原抓取回调。

        Returns:
            可用正文；失败或关闭后返回空字典。

        Raises:
            Exception: 原抓取回调的非超时异常。
        """
        self._counts["fetch_started"] += 1
        log_fetch_cache_event("enrichment_fetch_started", url)
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    fetched = await fetcher(deadline, required_length)
                    fetched, _ = prepare_fetched_content(fetched)
            except TimeoutError:
                log_fetch_cache_event("fetch_deadline_exceeded", url)
                fetched = {}
            if (
                self.closed or not has_sufficient_fetched_content(fetched, required_length)
                or has_pdf_magic(fetched)
            ):
                self._counts["fetch_failed"] += 1
                log_fetch_cache_event("enrichment_fetch_failed", url)
                return {}
            stored = copy.deepcopy(fetched)
            old = self._results.get(url)
            self._bytes += sys.getsizeof(stored["content"]) - (sys.getsizeof(old["content"]) if old else 0)
            self._results[url] = stored
            self._peak_bytes = max(self._peak_bytes, self._bytes)
            self._counts["fetch_succeeded"] += 1
            log_fetch_cache_event("enrichment_fetch_succeeded", url)
            log_fetch_cache_event("enrichment_cache_stored", url, **self.snapshot())
            return stored
        except BaseException:
            self._counts["fetch_failed"] += 1
            log_fetch_cache_event("enrichment_fetch_failed", url)
            raise
        finally:
            if self._inflight.get(url) is asyncio.current_task():
                self._inflight.pop(url, None)

    async def close(self) -> None:
        """关闭缓存、取消在途协程并输出关闭前的任务汇总。

        底层 to_thread 请求仍受原请求预算约束；closed 门禁阻止其迟到结果写回。
        """
        if self.closed:
            return
        self.closed = True
        flights = list(self._inflight.values())
        for flight in flights:
            flight.cancel()
        try:
            if flights:
                await asyncio.gather(*flights, return_exceptions=True)
        finally:
            log_fetch_cache_event("enrichment_cache_summary", **self.snapshot())
            self._inflight.clear()
            self._results.clear()
            self._bytes = 0
