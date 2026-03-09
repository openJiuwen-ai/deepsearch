# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import Optional, Generic, TypeVar, List, Dict, Union
import httpx
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen_deepsearch.common.common_constants import (
    MAX_URL_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
)

T = TypeVar("T")


class TavilySearchOptions(BaseModel):
    """Configuration options for Tavily search."""

    max_results: Optional[int] = 5
    search_depth: Optional[str] = "advanced"
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None
    include_answer: Optional[bool] = False
    include_raw_content: Optional[bool] = False
    include_images: Optional[bool] = False


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
        options: Optional[TavilySearchOptions] = None,
    ) -> Dict:
        """Run query through Tavily Search API and return raw result."""

        # Build API endpoint URL
        api_url = f"{self.search_url.get_secret_value()}/search"

        # Prepare request parameters
        if options is None:
            options = TavilySearchOptions()

        params = self._build_search_params(
            query=query,
            options=options,
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
        options: Optional[TavilySearchOptions] = None,
    ) -> List[Dict]:
        """Run query through Tavily Search API and return cleaned result"""

        if options is None:
            options = TavilySearchOptions()

        # Use default max_results from instance if not explicitly provided
        if options.max_results == 5:  # Default value in TavilySearchOptions
            options.max_results = self.max_web_search_results

        # Call raw search with options
        raw_data = self.raw_search_results(
            query=query,
            options=options,
        )

        # Extract and clean results from response
        search_results = raw_data.get("results", [])
        return self.clean_results(search_results)

    async def raw_search_results_async(
        self,
        query: str,
        options: Optional[TavilySearchOptions] = None,
    ) -> Dict:
        """Run query through Tavily Search API asynchronously."""

        # Prepare request data outside inner function
        request_url = f"{self.search_url.get_secret_value()}/search"

        if options is None:
            options = TavilySearchOptions()

        request_params = self._build_search_params(
            query=query,
            options=options,
        )

        ssl_verify_flag = self._get_ssl_verify_config()

        return await self._execute_async_http_request(
            request_url, request_params, ssl_verify_flag
        )

    async def aresults(
        self,
        query: str,
        options: Optional[TavilySearchOptions] = None,
    ) -> List[Dict]:
        """Run query through Tavily Search API asynchronously and return cleaned result."""

        if options is None:
            options = TavilySearchOptions()

        # Use default max_results from instance if not explicitly provided
        if options.max_results == 5:  # Default value in TavilySearchOptions
            options.max_results = self.max_web_search_results

        # Call async raw search with options
        raw_data = await self.raw_search_results_async(
            query=query,
            options=options,
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
        options: TavilySearchOptions,
    ) -> Dict:
        """Build parameters for Tavily API request."""
        return {
            "api_key": self.search_api_key.decode("utf-8"),
            "query": query,
            "max_results": options.max_results,
            "search_depth": options.search_depth,
            "include_domains": [] if options.include_domains is None else options.include_domains,
            "exclude_domains": [] if options.exclude_domains is None else options.exclude_domains,
            "include_answer": options.include_answer,
            "include_raw_content": options.include_raw_content,
            "include_images": options.include_images,
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
