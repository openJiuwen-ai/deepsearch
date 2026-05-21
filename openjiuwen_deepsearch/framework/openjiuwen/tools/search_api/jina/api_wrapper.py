# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Generic, Optional, TypeVar, Union

import httpx
import requests
from openjiuwen.core.common.security.ssl_utils import SslUtils
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH, MAX_URL_LENGTH

T = TypeVar("T")

DEFAULT_JINA_SEARCH_URL = "https://s.jina.ai"


class JinaSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for Jina Reader Search API at s.jina.ai."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    gl: str | None = None
    hl: str | None = None
    location: str | None = None
    page: int | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def model_post_init(self, __context: Any) -> None:
        """Apply Jina search options from extension."""
        ext = self.extension or {}
        if "gl" in ext:
            self.gl = ext["gl"]
        if "hl" in ext:
            self.hl = ext["hl"]
        if "location" in ext:
            self.location = ext["location"]
        if "page" in ext:
            self.page = int(ext["page"])

    def results(self, query: str) -> list[dict[str, Any]]:
        """Run query through Jina Search API and return cleaned result rows."""
        if not (query or "").strip():
            return []
        headers, payload, url, verify = self._prepare_search_request_data(query)
        response = requests.post(url, headers=headers, json=payload, verify=verify, timeout=30)
        response.raise_for_status()
        return self._parsed_results(response.json())

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        """Run query through Jina Search API asynchronously."""
        if not (query or "").strip():
            return []
        headers, payload, url, verify = self._prepare_search_request_data(query)
        async with httpx.AsyncClient(verify=verify, timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return self._parsed_results(response.json())

    def _prepare_search_request_data(self, query: str) -> tuple[dict[str, str], dict[str, Any], str, Union[str, bool]]:
        """Prepare headers, JSON payload, target URL, and SSL verification."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        api_key = self._api_key_to_str().strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "q": query,
            "num": self.max_web_search_results,
        }
        for key in ("gl", "hl", "location", "page"):
            value = getattr(self, key)
            if value is not None and value != "":
                payload[key] = value

        ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
        verify = ssl_cert if ssl_verify else False
        return headers, payload, f"{self._resolved_search_url()}/", verify

    def _parsed_results(self, raw: Any) -> list[dict[str, Any]]:
        """Normalize Jina JSON response data into research-compatible rows."""
        if isinstance(raw, dict):
            items = raw.get("data", [])
        else:
            items = raw
        if not isinstance(items, list):
            return []

        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            title = str(item.get("title") or url).strip()
            content = str(item.get("content") or item.get("description") or "").strip()
            results.append(
                {
                    "title": title[:MAX_SEARCH_CONTENT_LENGTH],
                    "url": url[:MAX_URL_LENGTH],
                    "content": content[:MAX_SEARCH_CONTENT_LENGTH],
                    "source": "jina",
                }
            )
        return results

    def _resolved_search_url(self) -> str:
        """Return configured URL or Jina Search's public default URL."""
        if self.search_url is None:
            return DEFAULT_JINA_SEARCH_URL
        if hasattr(self.search_url, "get_secret_value"):
            configured = self.search_url.get_secret_value()
        else:
            configured = str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        return configured or DEFAULT_JINA_SEARCH_URL

    def _api_key_to_str(self) -> str:
        """Decode configured API key."""
        value = self.search_api_key
        if isinstance(value, bytearray):
            return value.decode("utf-8")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")
