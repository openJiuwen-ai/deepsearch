# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import Optional, Generic, TypeVar, List, Dict, Union
import httpx
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from jiuwen_deepsearch.common.common_constants import (
    MAX_URL_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
)

T = TypeVar("T")


class TavilySearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper class for Tavily Search API"""

    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: dict = None

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def raw_search_results(
        self,
        query: str,
        max_results: Optional[int] = 5,
        search_depth: Optional[str] = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_answer: Optional[bool] = False,
        include_raw_content: Optional[bool] = False,
        include_images: Optional[bool] = False,
    ) -> Dict:
        """Run query through Tavily Search API and return raw result."""

        # Build API endpoint URL
        api_url = f"{self.search_url.get_secret_value()}/search"

        # Prepare request parameters
        params = self._build_search_params(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_answer=include_answer,
            include_raw_content=include_raw_content,
            include_images=include_images,
        )

        # Configure SSL verification
        verify = self._get_ssl_verify_config()

        # Execute HTTP request
        response = requests.post(api_url, json=params, verify=verify)
        response.raise_for_status()  # Raise exception for non-2xx status codes

        # Return parsed JSON response
        return response.json()

    def results(
        self,
        query: str,
        search_depth: Optional[str] = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_answer: Optional[bool] = False,
        include_raw_content: Optional[bool] = False,
        include_images: Optional[bool] = False,
    ) -> List[Dict]:
        """Run query through Tavily Search API and return cleaned result"""

        # Call raw search with default max_results from instance
        raw_data = self.raw_search_results(
            query=query,
            max_results=self.max_web_search_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_answer=include_answer,
            include_raw_content=include_raw_content,
            include_images=include_images,
        )

        # Extract and clean results from response
        search_results = raw_data.get("results", [])
        return self.clean_results(search_results)

    async def raw_search_results_async(
        self,
        query: str,
        max_results: Optional[int] = 5,
        search_depth: Optional[str] = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_answer: Optional[bool] = False,
        include_raw_content: Optional[bool] = False,
        include_images: Optional[bool] = False,
    ) -> Dict:
        """Run query through Tavily Search API asynchronously."""

        # Prepare request data outside inner function
        request_url = f"{self.search_url.get_secret_value()}/search"

        request_params = self._build_search_params(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_answer=include_answer,
            include_raw_content=include_raw_content,
            include_images=include_images,
        )

        ssl_verify_flag = self._get_ssl_verify_config()

        return await self._execute_async_http_request(
            request_url, request_params, ssl_verify_flag
        )

    async def aresults(
        self,
        query: str,
        search_depth: Optional[str] = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_answer: Optional[bool] = False,
        include_raw_content: Optional[bool] = False,
        include_images: Optional[bool] = False,
    ) -> List[Dict]:
        """Run query through Tavily Search API asynchronously and return cleaned result."""

        # Call async raw search with default max_results
        raw_data = await self.raw_search_results_async(
            query=query,
            max_results=self.max_web_search_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_answer=include_answer,
            include_raw_content=include_raw_content,
            include_images=include_images,
        )

        # Extract and clean results from response
        search_results = raw_data.get("results", [])
        return self.clean_results(search_results)

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

            # Add raw_content if present
            raw_content = result.get("raw_content")
            if raw_content:
                cleaned_result["raw_content"] = raw_content
            cleaned_results.append(cleaned_result)

        return cleaned_results

    def _build_search_params(
        self,
        query: str,
        max_results: Optional[int] = 5,
        search_depth: Optional[str] = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_answer: Optional[bool] = False,
        include_raw_content: Optional[bool] = False,
        include_images: Optional[bool] = False,
    ) -> Dict:
        """Build parameters for Tavily API request."""
        return {
            "api_key": self.search_api_key.decode("utf-8"),
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_domains": [] if include_domains is None else include_domains,
            "exclude_domains": [] if exclude_domains is None else exclude_domains,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
            "include_images": include_images,
        }

    def _get_ssl_verify_config(self) -> Union[str, bool]:
        """Get SSL verification configuration."""
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )
        return ssl_cert if ssl_verify else False

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
