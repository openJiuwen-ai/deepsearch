# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Union

from openjiuwen.core.common.security.ssl_utils import SslUtils

DEFAULT_PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"
ATOM_NAMESPACE = "http" + "://www.w3.org/2005/Atom"
PUBMED_DEFAULT_MIN_INTERVAL_SECONDS = 1.0 / 3.0
PUBMED_API_KEY_MIN_INTERVAL_SECONDS = 1.0 / 10.0
ARXIV_MIN_INTERVAL_SECONDS = 3.0


class ScholarlySearchResponseError(RuntimeError):
    """Raised when a scholarly search API returns a malformed or unexpected response."""


class SharedIntervalRateLimiter:
    """Process-local sync/async limiter that spaces requests by a fixed interval."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._next_available_at = 0.0

    def _reserve_delay(self) -> float:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_available_at - now)
            reserved_at = now + delay
            self._next_available_at = reserved_at + self.min_interval_seconds
            return delay

    def acquire(self) -> None:
        delay = self._reserve_delay()
        if delay > 0:
            time.sleep(delay)

    async def aacquire(self) -> None:
        delay = self._reserve_delay()
        if delay > 0:
            await asyncio.sleep(delay)


pubmed_default_rate_limiter = SharedIntervalRateLimiter(PUBMED_DEFAULT_MIN_INTERVAL_SECONDS)
pubmed_api_key_rate_limiter = SharedIntervalRateLimiter(PUBMED_API_KEY_MIN_INTERVAL_SECONDS)
arxiv_rate_limiter = SharedIntervalRateLimiter(ARXIV_MIN_INTERVAL_SECONDS)


def truncate(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def ssl_verify() -> Union[str, bool]:
    ssl_verify_value, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
    return ssl_cert if ssl_verify_value else False


def pubmed_rate_limiter(has_api_key: bool) -> SharedIntervalRateLimiter:
    return pubmed_api_key_rate_limiter if has_api_key else pubmed_default_rate_limiter
