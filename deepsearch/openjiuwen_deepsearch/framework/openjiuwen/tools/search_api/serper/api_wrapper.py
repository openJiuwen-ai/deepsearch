# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
import os
import asyncio
from typing import Any, Literal, Optional, Generic, TypeVar, List, Dict, Union
import httpx
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_search_service_url

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_SERPER_SEARCH_URL = "https://google.serper.dev"


class GoogleSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for Serper.dev Google Search API."""

    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    gl: str = "us"
    hl: str = "en"
    type: Literal["news", "search", "places", "images"] = "search"
    result_key_for_type: dict = {
        "news": "news",
        "places": "places",
        "images": "images",
        "search": "organic",
    }

    tbs: Optional[str] = None

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
        if "type" in ext and ext["type"] in self.result_key_for_type:
            self.type = ext["type"]
        if "tbs" in ext:
            self.tbs = ext["tbs"]

    def results(self, query: str) -> List[Dict]:
        """Run query through Serper GoogleSearch API."""
        return self.google_search_results(search_term=query)

    async def aresults(self, query: str) -> List[Dict]:
        """Run query through Serper GoogleSearch API asynchronously."""
        return await self.async_google_search_results(search_term=query)

    def google_search_results(self, search_term: str) -> Any:
        """Run query through Serper GoogleSearch API and parse result."""
        return self._execute_search_request(
            search_term=search_term, is_async=False
        )

    async def async_google_search_results(self, search_term: str) -> Any:
        """Run query through Serper GoogleSearch API asynchronously and parse result."""
        return await self._execute_search_request(
            search_term=search_term, is_async=True
        )

    def _prepare_search_request_data(self, search_term: str) -> tuple[dict, dict, str, Union[str, bool]]:
        """Prepare common data for search requests."""
        headers = {
            "X-API-KEY": self.search_api_key.decode("utf-8") or "",
            "Content-Type": "application/json",
        }
        url = f"{self._resolved_search_url()}/{self.type}"
        params: Dict[str, Any] = {
            "q": search_term,
            "gl": self.gl,
            "hl": self.hl,
            "num": self.max_web_search_results,
        }
        if self.tbs is not None:
            params["tbs"] = self.tbs
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )
        verify = ssl_cert if ssl_verify else False

        return headers, params, url, verify

    def _parsed_results(self, raw: Any) -> List[Dict]:
        """Take items from Serper JSON for the given endpoint."""
        if not isinstance(raw, dict):
            return []
        key = self.result_key_for_type.get(self.type, "organic")
        items = raw.get(key)
        if not isinstance(items, list):
            return []
        return items

    def _resolved_search_url(self) -> str:
        """Return configured URL or Serper's public default URL."""
        if self.search_url is None:
            return DEFAULT_SERPER_SEARCH_URL
        if hasattr(self.search_url, "get_secret_value"):
            configured = self.search_url.get_secret_value()
        else:
            configured = str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        if not configured:
            return DEFAULT_SERPER_SEARCH_URL
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
        response = requests.post(url, headers=headers, json=params, verify=verify)
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
