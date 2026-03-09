# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import uuid
from typing import Dict, Optional, Generic, TypeVar, List
from datetime import datetime, timezone
import logging
import aiohttp
import requests

from pydantic import BaseModel, ConfigDict, SecretStr
from openjiuwen.core.common.security.ssl_utils import SslUtils

logger = logging.getLogger(__name__)

T = TypeVar("T")


class XunfeiSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper around the Search API"""
    search_api_key: bytearray = None
    search_url: SecretStr = None
    max_web_search_results: int = 5
    extension: dict = {}

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow'
    )

    def results(self, query: str) -> List[Dict]:
        """Run query through InternalSearch."""
        return self._search_api_results(
            query,
            num=self.max_web_search_results,
            teller_id=self.extension.get("tellerId", ""),
        )

    async def aresults(self, query: str) -> List[Dict]:
        """Run query through InternalSearch."""
        results = await self._async_search_api_results(
            query,
            num=self.max_web_search_results,
            teller_id=self.extension.get("tellerId", ""),
        )
        return results

    def build_search_headers(self, search_term: str, num: int, teller_id: str):
        """Build headers for the search request."""
        search_headers = {
            "x-token": self.search_api_key.decode('utf-8') or "",
            "Content-Type": "application/json",
        }
        search_url = self.search_url.get_secret_value()
        search_data = {
            "chatId": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "dialogueId": str(uuid.uuid4()),
            "kbId": self.extension.get("kbId", ""),
            "multiTurn": False,
            "question": search_term,
            "tellerId": teller_id,
            "topn": num,
            "justSearch": True
        }

        return search_headers, search_url, search_data

    def _format_result_message(self, event: Optional[str], data: list, id_: Optional[str]) -> Dict:
        """Format a single result message from parsed data."""
        full_data = "\n".join(data)
        try:
            parsed_data = json.loads(full_data)
        except json.JSONDecodeError:
            parsed_data = full_data

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event or "message",
            "data": parsed_data,
            "id": id_
        }

    def _filter_results(self, result_list: list, num: int) -> list:
        """Filter and return final results."""
        res = result_list[2].get("data", {}).get("data", {}).get("data", {})
        return res[:num] if isinstance(res, list) else []

    def _make_search_request(self, search_term: str, num: int, teller_id: str) -> requests.Response:
        """Make search API request and return response."""
        search_headers, search_url, search_data = self.build_search_headers(search_term, num, teller_id)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
        verify = ssl_cert if ssl_verify else False
        response = requests.post(search_url, headers=search_headers, json=search_data, verify=verify)

        if response.status_code != 200:
            logger.error(f"Request search failed! Status code: {response.status_code}")
            response.raise_for_status()

        return response

    def _parse_response_lines(self, response: requests.Response) -> list:
        """Parse streaming response lines into structured messages."""
        result_list = []
        current_event = None
        current_data = []
        current_id = None

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                if current_data:
                    result_list.append(
                        self._format_result_message(current_event, current_data, current_id)
                    )
                    current_event = current_id = None
                    current_data = []
                continue

            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                current_data.append(line[5:].strip())
            elif line.startswith("id:"):
                current_id = line[3:].strip()

        return result_list

    def _search_api_results(
            self, search_term: str, num: int, teller_id: str
    ) -> List[Dict]:
        """Main method coordinating the search API flow."""
        response = self._make_search_request(search_term, num, teller_id)
        result_list = self._parse_response_lines(response)
        return self._filter_results(result_list, num)

    async def _async_make_search_request(
            self, search_term: str, num: int, teller_id: str
    ) -> tuple[bool, bytes]:
        """Make search API request asynchronously and return response content."""
        search_headers, search_url, search_data = self.build_search_headers(search_term, num, teller_id)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
        buffer = b""
        if ssl_verify:
            connector = aiohttp.TCPConnector(limit=2 ** 32, ssl=SslUtils.create_strict_ssl_context(ssl_cert))
        else:
            connector = aiohttp.TCPConnector(limit=2 ** 32, ssl=ssl_verify)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                    search_url, json=search_data, headers=search_headers, raise_for_status=False
            ) as response:
                if response.status != 200:
                    logger.error(f"Request search failed! Status code: {response.status}")
                    return False, buffer

                while True:
                    chunk = await response.content.read(4096)
                    if not chunk:
                        break
                    buffer += chunk

        return True, buffer

    async def _async_parse_response_content(self, buffer: bytes) -> list:
        """Parse streaming response content into structured messages."""
        try:
            content = buffer.decode('utf-8')
        except UnicodeDecodeError:
            content = buffer.decode('gbk', errors='replace')

        result_list = []
        current_event = None
        current_data = []
        current_id = None

        lines = content.split('\n')
        for line in lines:
            line = line.strip()

            if not line:
                if current_data:
                    result_list.append(
                        self._format_result_message(current_event, current_data, current_id)
                    )
                    current_event = current_id = None
                    current_data = []
                continue

            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                current_data.append(line[5:].strip())
            elif line.startswith("id:"):
                current_id = line[3:].strip()

        return result_list

    async def _async_search_api_results(
            self, search_term: str, num: int, teller_id: str
    ) -> List[Dict]:
        """Main method coordinating the async search API flow."""
        success, buffer = await self._async_make_search_request(search_term, num, teller_id)
        if not success or not buffer:
            return []

        result_list = await self._async_parse_response_content(buffer)
        if not result_list:
            return []

        return self._filter_results(result_list, num)
