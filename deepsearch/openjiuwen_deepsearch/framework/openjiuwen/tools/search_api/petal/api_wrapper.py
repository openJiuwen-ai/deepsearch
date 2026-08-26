# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import Any, Generic, TypeVar, List, Dict, Optional
import logging
import aiohttp
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

# 默认请求超时时间：30秒（防止无界等待）
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
# 默认连接超时时间：10秒
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

T = TypeVar("T")


class PetalSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for Petal Search API."""
    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    include_raw_content: bool = True
    freshness: str = "noLimit"

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow'
    )

    def model_post_init(self, __context: Any) -> None:
        """Apply engine-specific options from ``extension``."""
        ext = self.extension
        if not ext:
            return
        if "content" in ext:
            self.include_raw_content = bool(ext["content"])
        if "freshness" in ext:
            self.freshness = ext["freshness"]

    def results(self, query: str) -> List[Dict]:
        """Run query through Internal Search"""
        return self._search_api_results(
            query,
            num=self.max_web_search_results,
        )

    async def aresults(self, query: str) -> List[Dict]:
        """Run query through Internal Search"""
        return await self._async_search_api_results(
            query,
            num=self.max_web_search_results,
        )

    def build_headers(self, search_term: str):
        """Build headers for search requests."""
        search_api_key = self.search_api_key.decode('utf-8')
        search_headers = {
            "Content-Type": "application/json",
        }
        # 判断search_api_key格式
        if search_api_key.strip().startswith('{') and search_api_key.strip().endswith('}'):
            # 可能是json字符串，尝试解析
            temp_headers = json.loads(search_api_key)
            # 如果是Basic认证，直接合并
            auth = temp_headers.get("Authorization") if isinstance(temp_headers, dict) else ""
            if auth.startswith("Basic"):
                search_headers.update(temp_headers)
            else:
                # 不是Basic，无效的auth信息
                logger.error("invalid search_api_key format")
        else:
            # 不是json字符串，直接按Bearer逻辑
            authorization = search_api_key if search_api_key.startswith("Bearer") else f"Bearer {search_api_key}"
            search_headers['Authorization'] = authorization

        search_url = self.search_url.get_secret_value()
        search_data = {
            "query": search_term,
            "content": self.include_raw_content,
            "freshness": self.freshness,
        }
        return search_headers, search_url, search_data

    def _search_api_results(
            self, search_term: str, num: int
    ) -> List[Dict]:
        search_headers, search_url, search_data = self.build_headers(search_term)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
        verify = ssl_cert if ssl_verify else False

        response = requests.post(
            url=search_url,
            headers=search_headers,
            json=search_data,
            verify=verify,
            timeout=(DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        )

        if response.status_code != 200:
            logger.error(f"Request search failed! Status code: {response.status_code}")
            response.raise_for_status()

        results = response.json()
        result_list = results.get('web_pages', [])

        if isinstance(result_list, list):
            return result_list[:num]

        if LogManager.is_sensitive():
            logger.error(f"Unexpected Search request response!")
        else:
            logger.error(f"Unexpected response! original result: {result_list}")
        return []

    async def _async_search_api_results(
            self, search_term: str, num: int
    ) -> List[Dict]:
        result_list = []
        search_headers, search_url, search_data = self.build_headers(search_term)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])

        if ssl_verify:
            connector = aiohttp.TCPConnector(
                ssl=SslUtils.create_strict_ssl_context(ssl_cert)
            )
        else:
            connector = aiohttp.TCPConnector(ssl=ssl_verify)

        try:
            timeout = aiohttp.ClientTimeout(
                total=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                connect=DEFAULT_CONNECT_TIMEOUT_SECONDS,
                sock_read=DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=True) as session:
                async with session.post(
                        url=search_url,
                        headers=search_headers,
                        json=search_data
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

        result_list = results.get('web_pages', [])

        if isinstance(result_list, list):
            return result_list[:num]

        if LogManager.is_sensitive():
            logger.error(f"Unexpected Search request response!")
        else:
            logger.error(f"Unexpected response! original result: {result_list}")
        return []
