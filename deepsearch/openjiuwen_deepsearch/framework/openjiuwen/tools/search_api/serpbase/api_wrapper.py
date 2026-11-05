# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging

import httpx
import requests
from functools import cached_property
from typing import Any, Dict, Generic, List, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_search_service_url

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_SERPBASE_SEARCH_URL = "https://api.serpbase.dev"
GOOGLE_SEARCH_PATH = "/google/search"

#: 业务状态码，HTTP 200 不代表成功
SERPBASE_STATUS_SUCCESS = 0
SERPBASE_STATUS_INVALID_API_KEY = 1001
SERPBASE_STATUS_NO_CREDITS = 1020
SERPBASE_STATUS_RATE_LIMITED = 1029
SERPBASE_TRANSIENT_STATUS_CODES = frozenset({1500, 1501, 1502, 1503, 1504})


class SerpBaseSearchResponseError(RuntimeError):
    """Raised when SerpBase answers with HTTP 200 but reports a non-zero business status."""

    def __init__(self, status: int, message: str = "", request_id: str = ""):
        self.status = status
        self.request_id = request_id
        detail = f"SerpBase search failed with status {status}"
        if message:
            detail = f"{detail}: {message}"
        if request_id:
            detail = f"{detail} (request_id={request_id})"
        super().__init__(detail)

    @property
    def retryable(self) -> bool:
        """Rate limiting and the 1500-1504 range are transient server-side conditions."""
        return self.status == SERPBASE_STATUS_RATE_LIMITED or self.status in SERPBASE_TRANSIENT_STATUS_CODES


class SerpBaseSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for SerpBase Google web search API (https://serpbase.dev/docs).

    The API key is sent in the ``X-API-Key`` header only; SerpBase rejects
    credentials passed in the query string. The key is never logged.
    """

    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    gl: str = "us"
    hl: str = "en"

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def model_post_init(self, __context: Any) -> None:
        """Apply engine-specific options from ``extension``"""
        ext = self.extension
        if not ext:
            return
        if "gl" in ext:
            self.gl = ext["gl"]
        if "hl" in ext:
            self.hl = ext["hl"]

        # 预解析 search_url 以避免在 async 路径中首次触发同步 DNS 解析
        _ = self._resolved_search_url

    def results(self, query: str) -> List[Dict]:
        """Run query through SerpBase Google search API."""
        return self.google_search_results(search_term=query)

    async def aresults(self, query: str) -> List[Dict]:
        """Run query through SerpBase Google search API asynchronously."""
        return await self.async_google_search_results(search_term=query)

    def google_search_results(self, search_term: str) -> Any:
        """Run query through SerpBase Google search API and parse result."""
        return self._execute_search_request(
            search_term=search_term, is_async=False
        )

    async def async_google_search_results(self, search_term: str) -> Any:
        """Run query through SerpBase Google search API asynchronously and parse result."""
        return await self._execute_search_request(
            search_term=search_term, is_async=True
        )

    def _prepare_search_request_data(self, search_term: str) -> tuple[dict, dict, str, Union[str, bool]]:
        """Prepare common data for search requests."""
        headers = {
            "X-API-Key": self.search_api_key.decode("utf-8") or "",
            "Content-Type": "application/json",
        }
        url = f"{self._resolved_search_url}{GOOGLE_SEARCH_PATH}"
        params: Dict[str, Any] = {
            "q": search_term,
            "gl": self.gl,
            "hl": self.hl,
            "num": self.max_web_search_results,
        }
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )
        verify = ssl_cert if ssl_verify else False

        return headers, params, url, verify

    def _parsed_results(self, raw: Any) -> List[Dict]:
        """Validate the SerpBase envelope and return its ``organic`` items."""
        if not isinstance(raw, dict):
            return []
        try:
            status = int(raw.get("status"))
        except (TypeError, ValueError):
            status = -1
        if status != SERPBASE_STATUS_SUCCESS:
            request_id = str(raw.get("request_id") or "")
            message = str(raw.get("message") or raw.get("error") or "")
            # 只记录状态码与 request_id，避免把密钥写入日志
            logger.error(
                "Request search failed! Business status: %s, request_id: %s", status, request_id
            )
            raise SerpBaseSearchResponseError(status, message, request_id)
        items = raw.get("organic")
        if not isinstance(items, list):
            return []
        return items

    @cached_property
    def _resolved_search_url(self) -> str:
        """Return configured URL or SerpBase's public default URL."""
        if self.search_url is None:
            return DEFAULT_SERPBASE_SEARCH_URL
        if hasattr(self.search_url, "get_secret_value"):
            configured = self.search_url.get_secret_value()
        else:
            configured = str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        if not configured:
            return DEFAULT_SERPBASE_SEARCH_URL
        validate_search_service_url(configured)
        return configured

    def _execute_search_request(self, search_term: str, is_async: bool = False) -> Any:
        """Execute search request with optional async support."""
        headers, params, url, verify = self._prepare_search_request_data(search_term)

        if is_async:
            return self._async_search(headers, params, url, verify)
        return self._sync_search(headers, params, url, verify)

    def _sync_search(
        self,
        headers: dict,
        params: dict,
        url: str,
        verify: Union[str, bool],
    ) -> List[Dict]:
        """Execute synchronous search request."""
        response = requests.post(url, headers=headers, json=params, verify=verify, timeout=30)
        if response.status_code != 200:
            logger.error(f"Request search failed! Status code: {response.status_code}")
            response.raise_for_status()
        return self._parsed_results(response.json())

    async def _async_search(
        self,
        headers: dict,
        params: dict,
        url: str,
        verify: Union[str, bool],
    ) -> List[Dict]:
        """Execute asynchronous search request."""
        async with httpx.AsyncClient(verify=verify, timeout=30) as client:
            response = await client.post(url, json=params, headers=headers)
            response.raise_for_status()
            return self._parsed_results(response.json())
