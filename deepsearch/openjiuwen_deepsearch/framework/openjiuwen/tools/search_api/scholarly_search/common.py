# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, TypeVar, Union

import httpx
import requests

from openjiuwen.core.common.security.ssl_utils import SslUtils

DEFAULT_PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"
ATOM_NAMESPACE = "http" + "://www.w3.org/2005/Atom"
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_DELAY_SECONDS = 30.0
R = TypeVar("R")


class ScholarlySearchResponseError(RuntimeError):
    """Raised when a scholarly search API returns a malformed or unexpected response."""


class ServiceRequestControl:
    """Process-local request schedule shared by sync and async wrappers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_rate_release_at = 0.0
        self._cooldown_until = 0.0

    def _release_or_delay(self, requests_per_second: float | None) -> float:
        with self._lock:
            now = time.monotonic()
            release_at = self._cooldown_until
            if requests_per_second is not None:
                interval = 1.0 / max(0.1, float(requests_per_second))
                release_at = max(release_at, self._last_rate_release_at + interval)
            delay = max(0.0, release_at - now)
            if delay == 0 and requests_per_second is not None:
                self._last_rate_release_at = now
            return delay

    def wait_sync(self, requests_per_second: float | None = None) -> None:
        while True:
            delay = self._release_or_delay(requests_per_second)
            if delay <= 0:
                return
            time.sleep(delay)

    async def wait_async(self, requests_per_second: float | None = None) -> None:
        while True:
            delay = self._release_or_delay(requests_per_second)
            if delay <= 0:
                return
            await asyncio.sleep(delay)

    def defer(self, delay_seconds: float) -> None:
        with self._lock:
            self._cooldown_until = max(
                self._cooldown_until,
                time.monotonic() + max(0.0, float(delay_seconds)),
            )

    def reset(self) -> None:
        with self._lock:
            self._last_rate_release_at = 0.0
            self._cooldown_until = 0.0


class ServiceConcurrencyLimiter:
    """Small process-local concurrency cap usable from sync and async code."""

    def __init__(self, max_concurrency: int) -> None:
        self._semaphore = threading.BoundedSemaphore(max(1, int(max_concurrency)))

    @contextmanager
    def sync_slot(self):
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    @asynccontextmanager
    async def async_slot(self):
        while not self._semaphore.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            self._semaphore.release()


NCBI_REQUEST_CONTROL = ServiceRequestControl()
ARXIV_REQUEST_CONTROL = ServiceRequestControl()
ARXIV_DOWNLOAD_LIMITER = ServiceConcurrencyLimiter(2)


def reset_scholarly_request_controls() -> None:
    """Reset process-local schedules, primarily for isolated wrapper lifecycles and tests."""
    NCBI_REQUEST_CONTROL.reset()
    ARXIV_REQUEST_CONTROL.reset()


def http_status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_transient_connection_error(error: BaseException) -> bool:
    return isinstance(error, (
        httpx.TransportError,
        requests.ConnectionError,
        requests.Timeout,
    ))


def _retry_after_delay(response: Any) -> float:
    if int(getattr(response, "status_code", 0) or 0) == 429:
        raw = str((getattr(response, "headers", {}) or {}).get("Retry-After", "")).strip()
        if raw:
            try:
                return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(raw)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return min(
                        MAX_RETRY_DELAY_SECONDS,
                        max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
    return 1.0


async def async_request_once(
        request: Callable[[], Awaitable[R]],
        before_attempt: Callable[[], Awaitable[None]],
        *,
        control: ServiceRequestControl,
) -> R:
    await before_attempt()
    response = await request()
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        control.defer(_retry_after_delay(response))
    response.raise_for_status()
    return response


def sync_request_once(
        request: Callable[[], R],
        before_attempt: Callable[[], None],
        *,
        control: ServiceRequestControl,
) -> R:
    before_attempt()
    response = request()
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        control.defer(_retry_after_delay(response))
    response.raise_for_status()
    return response


def truncate(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def ssl_verify() -> Union[str, bool]:
    ssl_verify_value, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
    return ssl_cert if ssl_verify_value else False


def parse_boolean_extension(ext: dict, key: str, *, default: bool) -> bool:
    if key not in ext:
        return default
    value = ext[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{key} must be a boolean or 'true'/'false' string")


def apply_full_text_extension_config(wrapper: Any, extension: dict | None) -> None:
    ext = extension or {}
    wrapper.fetch_full_text = parse_boolean_extension(
        ext,
        "scholarly_fetch_full_text",
        default=wrapper.fetch_full_text,
    )
    mappings = {
        "scholarly_max_full_text_results": ("max_full_text_results", 0),
        "scholarly_full_text_timeout_seconds": ("full_text_timeout_seconds", 1),
        "scholarly_max_full_text_length": ("max_full_text_length", 1),
    }
    for extension_key, (attribute, minimum) in mappings.items():
        if extension_key not in ext:
            continue
        try:
            value = max(minimum, int(ext[extension_key]))
        except (TypeError, ValueError):
            continue
        setattr(wrapper, attribute, value)
