# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, ClassVar, Generic, Optional, TypeVar

from openjiuwen.harness.tools.web_tools import WebFetchWebpageTool, WebPaidSearchTool
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import (
    MAX_COLLECTOR_DOC_CONTENT_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
    MAX_URL_LENGTH,
)
from openjiuwen_deepsearch.utils.common_utils.text_utils import truncate_string

logger = logging.getLogger(__name__)

T = TypeVar("T")

HARNESS_FETCHED_CONTENT_MAX_LENGTH = MAX_COLLECTOR_DOC_CONTENT_LENGTH


class WebFetchWebpageAdapter(WebFetchWebpageTool):
    """Public adapter for harness webpage fetching."""

    @classmethod
    def fetch_webpage_sync(cls, url: str, timeout_seconds: int) -> dict[str, str | int]:
        """Fetch webpage content through the inherited web_tools implementation."""
        return dict(cls._fetch_webpage_sync(url, timeout_seconds))

    @classmethod
    def fetch_via_jina_reader_sync(cls, url: str, timeout_seconds: int) -> dict[str, str | int]:
        """通过公开 Jina Reader 代理抓取网页正文。

        Args:
            url: 目标网页 URL。
            timeout_seconds: 请求超时时间，单位秒。

        Returns:
            包含 URL、状态码、标题和正文的抓取结果。
        """
        return dict(cls._fetch_via_jina_reader_sync(url, timeout_seconds))


_PROVIDER_KEY_ENV = {
    "bocha": "BOCHA_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}

# Only providers with web_tools URL override env vars are listed here.
_WEB_TOOLS_URL_OVERRIDE_ENV = {
    "bocha": "BOCHA_API_URL",
    "perplexity": "PPLX_API_URL",
}

_ENV_LOCK = threading.RLock()


@contextmanager
def _temporary_env(values: dict[str, str]):
    """Temporarily set environment variables for harness web_tools helpers."""
    with _ENV_LOCK:
        old_values = {key: os.environ.get(key) for key in values}
        try:
            for key, value in values.items():
                os.environ[key] = value
            yield
        finally:
            for key, old_value in old_values.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value


class HarnessWebSearchAPIWrapper(BaseModel, Generic[T]):
    """Adapter from openjiuwen harness web_tools search to research web_search_tool."""

    provider: str = "auto"
    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None
    timeout_seconds: int | None = None
    fetch_webpage: bool = True

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    _provider_runner_names: ClassVar[dict[str, str]] = {
        "bocha": "_bocha_search_sync",
        "perplexity": "_perplexity_search_sync",
    }

    def model_post_init(self, __context: Any) -> None:
        """Apply runtime options from extension."""
        ext = self.extension or {}
        if ext.get("provider"):
            self.provider = str(ext["provider"]).strip().lower()
        if ext.get("timeout_seconds"):
            self.timeout_seconds = int(ext["timeout_seconds"])
        if "fetch_webpage" in ext:
            self.fetch_webpage = bool(ext["fetch_webpage"])

    @property
    def resolved_provider(self) -> str:
        """Return provider name normalized for harness web_tools search."""
        provider = (self.provider or "").strip().lower()
        if provider == "google":
            return "serper"
        return provider

    def results(self, query: str) -> list[dict[str, Any]]:
        """Run harness web search and return research-compatible search result rows."""
        query = (query or "").strip()
        if not query:
            return []

        provider = self.resolved_provider
        search_result = self._run_harness_search(query=query, provider=provider)
        answer = str(search_result.get("answer", "") or "").strip()
        urls = self._unique_urls(search_result.get("urls", []))[: self.max_web_search_results]

        results: list[dict[str, Any]] = []
        for url in urls:
            results.append(self._build_result_from_url(url=url, provider=provider, answer=answer))
        return results

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        """Run harness web search asynchronously."""
        return await asyncio.to_thread(self.results, query)

    def _run_harness_search(self, *, query: str, provider: str) -> dict[str, Any]:
        """Execute the selected harness web_tools search provider."""
        runner_name = self._provider_runner_names.get(provider)
        if not runner_name:
            raise ValueError(f"Unsupported harness web search provider: {provider}")

        env_values = {}
        api_key = self._api_key_to_str().strip()
        if api_key:
            env_values[_PROVIDER_KEY_ENV[provider]] = api_key
        configured_url = self._configured_search_url()
        if configured_url and provider not in _WEB_TOOLS_URL_OVERRIDE_ENV:
            logger.warning(
                "Configured search_url for provider %s is ignored because web_tools does not expose a URL override.",
                provider,
            )
        url_env = _WEB_TOOLS_URL_OVERRIDE_ENV.get(provider)
        if url_env and configured_url:
            env_values[url_env] = configured_url

        runner = getattr(WebPaidSearchTool, runner_name)
        timeout_seconds = self._resolved_timeout_seconds(provider=provider, minimum=10)
        max_results = max(1, min(int(self.max_web_search_results or 5), 20))

        with _temporary_env(env_values):
            return runner(query=query, max_results=max_results, timeout_seconds=timeout_seconds)

    def _build_result_from_url(self, *, url: str, provider: str, answer: str) -> dict[str, Any]:
        """Build a normalized result, fetching raw page content when possible."""
        normalized_url = str(url or "").strip()[:MAX_URL_LENGTH]
        fetched = self._fetch_url(normalized_url) if self.fetch_webpage else {}
        title = str(fetched.get("title", "") or "").strip() or normalized_url
        content = self._normalize_result_content(
            fetched_content=fetched.get("content", ""),
            fallback_content=answer or normalized_url,
        )
        return {
            "title": title[:MAX_SEARCH_CONTENT_LENGTH],
            "url": normalized_url,
            "content": content[:MAX_SEARCH_CONTENT_LENGTH],
            "source": provider,
        }

    @staticmethod
    def _normalize_result_content(fetched_content: Any, fallback_content: str) -> str:
        """Keep prefetched result content bounded before it enters downstream prompts."""
        content = str(fetched_content or "").strip() or str(fallback_content or "").strip()
        return truncate_string(content, HARNESS_FETCHED_CONTENT_MAX_LENGTH)

    def _fetch_url(self, url: str) -> dict[str, Any]:
        """Fetch URL text via web_tools webfetch, returning an empty dict on failure."""
        if not url:
            return {}
        try:
            timeout_seconds = self._resolved_timeout_seconds(provider=self.resolved_provider, minimum=5)
            return WebFetchWebpageAdapter.fetch_webpage_sync(url, timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Harness web search fetch failed for %s: %s", url, exc)
            return {}

    def _api_key_to_str(self) -> str:
        """Decode configured API key."""
        value = self.search_api_key
        if isinstance(value, bytearray):
            return value.decode("utf-8")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")

    def _configured_search_url(self) -> str:
        """Return project-level configured search_url, if any."""
        return self._secret_to_str(self.search_url).strip().rstrip("/")

    def _resolved_timeout_seconds(self, *, provider: str, minimum: int) -> int:
        """Return configured timeout for harness web_tools providers."""
        default_timeout = 60
        timeout_seconds = self.timeout_seconds if self.timeout_seconds is not None else default_timeout
        return max(minimum, min(int(timeout_seconds), 120))

    @staticmethod
    def _secret_to_str(value: SecretStr | str | None) -> str:
        """Convert SecretStr/string values to plain text."""
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")

    @staticmethod
    def _unique_urls(urls: Any) -> list[str]:
        """Return unique non-empty URLs preserving order."""
        seen: set[str] = set()
        unique: list[str] = []
        if not isinstance(urls, list):
            return unique
        for item in urls:
            url = str(item or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(url)
        return unique


class BochaSearchAPIWrapper(HarnessWebSearchAPIWrapper[T]):
    """Bocha harness web search adapter."""

    provider: str = "bocha"


class PerplexitySearchAPIWrapper(HarnessWebSearchAPIWrapper[T]):
    """Perplexity harness web search adapter."""

    provider: str = "perplexity"
