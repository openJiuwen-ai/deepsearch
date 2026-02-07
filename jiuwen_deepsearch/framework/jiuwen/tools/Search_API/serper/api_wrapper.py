# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from typing import Literal, Optional, Generic, TypeVar, Any, List, Dict, Union
import httpx
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils

logger = logging.getLogger(__name__)

T = TypeVar("T")


class GoogleSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for Serper.dev Google Search API."""

    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: dict = None

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

    def results(self, query: str, **kwargs: Any) -> List[Dict]:
        """Run query through Serper GoogleSearch API."""
        merged_kwargs = kwargs.copy()
        merged_kwargs.update(
            {
                "gl": self.gl,
                "hl": self.hl,
                "num": self.max_web_search_results,
                "tbs": self.tbs,
            }
        )
        results = self.google_search_results(
            search_term=query,
            search_type=self.type,
            **merged_kwargs,
        )
        return results

    async def aresults(self, query: str, **kwargs: Any) -> List[Dict]:
        """Run query through Serper GoogleSearch API asynchronously."""

        merged_kwargs = kwargs.copy()
        merged_kwargs.update(
            {
                "gl": self.gl,
                "hl": self.hl,
                "num": self.max_web_search_results,
                "tbs": self.tbs,
            }
        )
        results = await self.async_google_search_results(
            search_term=query,
            search_type=self.type,
            **merged_kwargs,
        )
        return results

    def google_search_results(
            self, search_term: str, search_type: str = "search", **kwargs: Any
    ) -> List[Dict]:
        """Run query through Serper GoogleSearch API and parse result."""
        return self._execute_search_request(
            search_term=search_term, search_type=search_type, is_async=False, **kwargs
        )

    async def async_google_search_results(
            self, search_term: str, search_type: str = "search", **kwargs: Any
    ) -> List[Dict]:
        """Run query through Serper GoogleSearch API asynchronously and parse result."""
        return await self._execute_search_request(
            search_term=search_term, search_type=search_type, is_async=True, **kwargs
        )

    def _prepare_search_request_data(
            self, search_term: str, search_type: str, **kwargs: Any
    ) -> tuple[dict, dict, str, Union[str, bool]]:
        """Prepare common data for search requests."""
        headers = {
            "X-API-KEY": self.search_api_key.decode("utf-8") or "",
            "Content-Type": "application/json",
        }
        url = f"{self.search_url.get_secret_value()}/{search_type}"
        params = {
            "q": search_term,
            **{key: value for key, value in kwargs.items() if value is not None},
        }
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )
        verify = ssl_cert if ssl_verify else False

        return headers, params, url, verify

    def _execute_search_request(
            self, search_term: str, search_type: str, is_async: bool = False, **kwargs: Any
    ) -> Any:
        """Execute search request with optional async support."""
        headers, params, url, verify = self._prepare_search_request_data(
            search_term, search_type, **kwargs
        )

        if is_async:
            return self._async_search(headers, params, url, verify)
        else:
            return self._sync_search(headers, params, url, verify)

    def _sync_search(
            self, headers: dict, params: dict, url: str, verify: Union[str, bool]
    ) -> List[Dict]:
        """Execute synchronous search request."""
        response = requests.post(url, headers=headers, params=params, verify=verify)
        if response.status_code != 200:
            logger.error(f"Request search failed! Status code: {response.status_code}")
            response.raise_for_status()
        return response.json()

    async def _async_search(
            self, headers: dict, params: dict, url: str, verify: Union[str, bool]
    ) -> List[Dict]:
        """Execute asynchronous search request."""
        async with httpx.AsyncClient(verify=verify, timeout=30) as client:
            response = await client.post(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
