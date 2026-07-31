# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Generic, TypeVar, List, Dict, Union, Optional
import httpx
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen_deepsearch.common.common_constants import (
    MAX_URL_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
)

T = TypeVar("T")

DEFAULT_TAVILY_SEARCH_URL = "https://api.tavily.com"


class TavilySearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper class for Tavily Search API"""

    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    # Tavily search options
    topic: str = "general"
    search_depth: str = "advanced"
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None
    include_answer: bool = False
    include_raw_content: bool = False
    include_images: bool = False
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def model_post_init(self, __context: Any) -> None:
        """Apply engine-specific options from ``extension``"""
        ext = self.extension
        if not ext:
            return
        if "topic" in ext:
            self.topic = ext["topic"]
        if "search_depth" in ext:
            self.search_depth = ext["search_depth"]
        if "include_domains" in ext:
            self.include_domains = ext["include_domains"]
        if "exclude_domains" in ext:
            self.exclude_domains = ext["exclude_domains"]
        if "include_answer" in ext:
            self.include_answer = ext["include_answer"]
        if "include_raw_content" in ext:
            self.include_raw_content = ext["include_raw_content"]
        if "include_images" in ext:
            self.include_images = ext["include_images"]

    @staticmethod
    async def _execute_async_http_request(
        url: str, params: Dict, verify: Union[str, bool]
    ) -> Dict:
        """Execute asynchronous HTTP request to Tavily API."""

        async with httpx.AsyncClient(verify=verify, timeout=30) as http_client:
            api_response = await http_client.post(url, json=params)

            if api_response.status_code not in (200, 201):
                error_msg = (
                    f"Error {api_response.status_code}: {api_response.reason_phrase}"
                )
                raise Exception(error_msg)
            response_text = api_response.text
            return json.loads(response_text)

    def raw_search_results(self, query: str) -> Dict:
        """Run query through Tavily Search API and return raw result."""

        # Build API endpoint URL
        api_url = f"{self._resolved_search_url()}/search"

        params = self._build_search_params(query=query)

        # Configure SSL verification
        verify = self._get_ssl_verify_config()

        # Execute HTTP request
        response = requests.post(api_url, json=params, verify=verify)
        response.raise_for_status()  # Raise exception for non-2xx status codes

        # Return parsed JSON response
        return response.json()

    def results(self, query: str) -> List[Dict]:
        """Run query through Tavily Search API and return cleaned result"""

        return self.clean_results(self._extract_search_results(self.raw_search_results(query=query)))

    async def raw_search_results_async(self, query: str) -> Dict:
        """Run query through Tavily Search API asynchronously."""

        request_url = f"{self._resolved_search_url()}/search"

        request_params = self._build_search_params(query=query)

        ssl_verify_flag = self._get_ssl_verify_config()

        return await self._execute_async_http_request(
            request_url, request_params, ssl_verify_flag
        )

    async def aresults(self, query: str) -> List[Dict]:
        """Run query through Tavily Search API asynchronously and return cleaned result."""
        raw_data = await self.raw_search_results_async(query=query)
        return self.clean_results(self._extract_search_results(raw_data))

    @staticmethod
    def _extract_search_results(raw_data: Any) -> List[Dict]:
        """校验并提取单路 Tavily 响应中的结果列表。

        Args:
            raw_data: Tavily 原始响应。

        Returns:
            结构合法的结果字典列表。

        Raises:
            ValueError: 响应不是字典、results 不是列表或列表元素不是字典时抛出。
        """
        if not isinstance(raw_data, dict):
            raise ValueError("Tavily response must be a dict")
        search_results = raw_data.get("results", [])
        if not isinstance(search_results, list) or any(not isinstance(item, dict) for item in search_results):
            raise ValueError("Tavily response results must be a list of dicts")
        return search_results

    def clean_results(self, results: List[Dict]) -> List[Dict]:
        """Clean results from Tavily Search API with structured json."""

        cleaned_results = []
        for result in results:
            # Create clean result entry with truncated fields
            cleaned_result = {
                "title": result.get("title", "")[:MAX_SEARCH_CONTENT_LENGTH],
                "url": result.get("url", "")[:MAX_URL_LENGTH],
                "content": result.get("content", "")[:MAX_SEARCH_CONTENT_LENGTH],
                "score": result.get("score", 0.0),
            }

            # Add raw_content if present, with length limit to prevent resource consumption
            raw_content = result.get("raw_content")
            if raw_content:
                cleaned_result["raw_content"] = raw_content[:MAX_SEARCH_CONTENT_LENGTH]
            # Tavily 官方结果字段 published_date 在 provider 边界归一化为统一来源日期。
            published_date = result.get("published_date")
            if published_date is not None and str(published_date).strip():
                normalized_date = self._normalize_published_date(published_date)
                if normalized_date:
                    cleaned_result["source_date"] = normalized_date
                    cleaned_result["source_date_type"] = "published"
            cleaned_results.append(cleaned_result)

        return cleaned_results

    @staticmethod
    def _normalize_published_date(value: Any) -> str:
        """将 Tavily 的 RFC 2822 或 ISO 发表时间归一化为 UTC 日期。

        Args:
            value: Tavily ``published_date`` 字段值。

        Returns:
            ISO 日期字符串；无法确认时返回空字符串。
        """
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            parsed = None
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return ""
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date().isoformat()

    def _build_search_params(self, query: str) -> Dict:
        """Build parameters for Tavily API request."""
        params = {
            "api_key": self.search_api_key.decode("utf-8"),
            "query": query,
            "max_results": self.max_web_search_results,
            "topic": self.topic,
            "search_depth": self.search_depth,
            "include_domains": [] if self.include_domains is None else self.include_domains,
            "exclude_domains": [] if self.exclude_domains is None else self.exclude_domains,
            "include_answer": self.include_answer,
            "include_raw_content": self.include_raw_content,
            "include_images": self.include_images,
        }
        if self.start_date:
            params["start_date"] = self.start_date
        if self.end_date:
            params["end_date"] = self.end_date
        return params

    def _get_ssl_verify_config(self) -> Union[str, bool]:
        """Get SSL verification configuration."""
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )
        return ssl_cert if ssl_verify else False

    def _resolved_search_url(self) -> str:
        """Return configured URL or Tavily's public default URL."""
        if self.search_url is None:
            return DEFAULT_TAVILY_SEARCH_URL
        if hasattr(self.search_url, "get_secret_value"):
            configured = self.search_url.get_secret_value()
        else:
            configured = str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        return configured or DEFAULT_TAVILY_SEARCH_URL
