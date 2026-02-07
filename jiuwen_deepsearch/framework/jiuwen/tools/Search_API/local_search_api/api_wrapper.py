# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Literal, Generic, TypeVar, List, Dict
import logging
import aiohttp
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LocalDatasetAPIWrapper(BaseModel, Generic[T]):
    """Wrapper around the Local Search API"""
    search_api_key: bytearray = bytearray("", "utf-8")
    search_url: SecretStr = None
    search_datasets: list = []
    max_local_search_results: int = 5
    recall_threshold: float = 0.5
    search_mode: Literal["doc", "keyword", "mix"] = "doc"
    knowledge_base_type: Literal["internal", "external"] = "internal"
    source: Literal["KooSearch", "LakeSearch"] = "KooSearch"
    extension: dict = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow'
    )

    def results(self, query: str) -> List[Dict]:
        """Run query through LocalSearch."""
        return self._search_api_results(
            query,
            num=self.max_local_search_results,
        )

    async def aresults(self, query: str) -> List[Dict]:
        """Run query through LocalSearch."""
        results = await self._async_search_api_results(
            query,
            num=self.max_local_search_results,
        )
        return results

    def build_headers(self):
        """Build headers for the search request."""
        search_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Auth-Token": f"{self.search_api_key.decode('utf-8')}",
        }
        return search_headers

    def build_request_params(self, search_term: str):
        """Build request params for the search request."""
        body_params = {
            "query": search_term,
        }

        query_params = {
            "repo_id": self.search_datasets,
            "top_k": self.max_local_search_results,
            "recall_threshold": self.recall_threshold,
            "search_mode": self.search_mode,
            "type": self.knowledge_base_type,
            "source": self.source,
        }
        return body_params, query_params

    def _search_api_results(
            self, search_term: str, num: int
    ) -> List[Dict]:
        result_list = []

        search_headers = self.build_headers()
        body_params, query_params = self.build_request_params(search_term)

        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
        verify = ssl_cert if ssl_verify else False

        try:
            response = requests.post(
                url=self.search_url.get_secret_value(),
                headers=search_headers,
                params=query_params,
                json=body_params,
                verify=verify,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            if LogManager.is_sensitive():
                logger.error(f"Search request failed!")
            else:
                logger.error(f"Search request failed! Error: {e}")
            return result_list

        results = response.json()
        result_list = results.get('output_list', [])

        if isinstance(result_list, list):
            return result_list[:num]

        if LogManager.is_sensitive():
            logger.error(f"Unexpected search request response!")
        else:
            logger.error(f"Unexpected response! original result: {result_list}")
        return []

    async def _async_search_api_results(
            self, search_term: str, num: int
    ) -> List[Dict]:
        result_list = []

        search_headers = self.build_headers()
        body_params, query_params = self.build_request_params(search_term)

        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])

        if ssl_verify:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
        else:
            connector = aiohttp.TCPConnector(ssl=ssl_verify)

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                        url=self.search_url.get_secret_value(),
                        headers=search_headers,
                        params=query_params,
                        json=body_params,
                ) as response:
                    if response.status not in (200, 201):
                        logger.error(f"Search request failed! Error: {response.status}")
                        return result_list

                    results = await response.json()
        except aiohttp.ClientError as e:
            if LogManager.is_sensitive():
                logger.error(f"Search request failed!")
            else:
                logger.error(f"Search request failed! Error: {e}")
            return result_list

        result_list = results.get('output_list', [])

        if isinstance(result_list, list):
            return result_list[:num]
        if LogManager.is_sensitive():
            logger.error(f"Unexpected search request response!")
        else:
            logger.error(f"Unexpected response! original result: {result_list}")
        return []
