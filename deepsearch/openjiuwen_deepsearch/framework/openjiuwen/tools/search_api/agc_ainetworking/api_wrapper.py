# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from datetime import UTC, datetime
from functools import cached_property
from typing import Any, Generic, TypeVar

import aiohttp
import requests
from openjiuwen.core.common.security.ssl_utils import SslUtils
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import (
    MAX_SEARCH_CONTENT_LENGTH,
    MAX_URL_LENGTH,
)
from openjiuwen_deepsearch.utils.common_utils.url_utils import (
    normalize_domains,
    validate_search_service_url,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 默认请求超时时间：30秒（防止无界等待）
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
# 默认连接超时时间：10秒
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
# 华为 AGC AI Networking sites 参数上限
MAX_SITES_NUM = 20

DEFAULT_AGC_AINETWORKING_SEARCH_URL = (
    "https://connect-api.cloud.huawei.com/api/aiNetworking/v1/webSearch"
)


class AgcAiNetworkingSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for Huawei AGC AI Networking webSearch API."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 5
    extension: dict | None = None

    sites: list[str] | None = None
    category: list[str] | None = None
    freshness: str = "noLimit"

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def model_post_init(self, __context: Any, /) -> None:
        """Apply engine-specific options from ``extension``."""
        ext = self.extension
        if ext:
            if "sites" in ext:
                self.sites = normalize_domains(ext["sites"])[:MAX_SITES_NUM]
            if "category" in ext:
                self.category = [
                    str(c).strip() for c in ext["category"] if str(c).strip()
                ]
            if "freshness" in ext:
                self.freshness = str(ext["freshness"])

        # 预解析 search_url 以避免在 async 路径中首次触发同步 DNS 解析；
        # 空 URL 解析为默认端点，无报错路径（同 jina）
        _ = self._resolved_search_url

    @cached_property
    def _resolved_search_url(self) -> str:
        """Return configured URL or AGC AI Networking default endpoint."""
        if self.search_url is None:
            return DEFAULT_AGC_AINETWORKING_SEARCH_URL
        if hasattr(self.search_url, "get_secret_value"):
            configured = self.search_url.get_secret_value()
        else:
            configured = str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        if not configured:
            return DEFAULT_AGC_AINETWORKING_SEARCH_URL
        # 默认端点为已知安全 URL，跳过 SSRF 校验；自定义 URL 需校验
        if configured != DEFAULT_AGC_AINETWORKING_SEARCH_URL:
            validate_search_service_url(configured)
        return configured

    def _api_key_to_str(self) -> str:
        """Decode configured API key."""
        value = self.search_api_key
        if isinstance(value, bytearray):
            return value.decode("utf-8")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")

    def _build_headers(self) -> dict[str, str]:
        """Build request headers; omit X-Api-Key when key is empty."""
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key_to_str().strip()
        if api_key:
            headers["X-Api-Key"] = api_key
        return headers

    def _build_request_body(self, query: str) -> dict[str, Any]:
        """Build JSON request body; sites/category only when non-empty."""
        body: dict[str, Any] = {
            "query": query,
            "count": min(max(self.max_web_search_results, 1), 50),
            "freshness": self.freshness or "noLimit",
        }
        if self.sites:
            body["sites"] = self.sites[:MAX_SITES_NUM]
        if self.category:
            body["category"] = self.category
        return body

    def results(self, query: str) -> list[dict]:
        """Run query through AGC AI Networking webSearch (sync)."""
        if not (query or "").strip():
            return []
        url = self._resolved_search_url
        headers = self._build_headers()
        body = self._build_request_body(query)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )
        verify = ssl_cert if ssl_verify else False

        response = requests.post(
            url=url,
            headers=headers,
            json=body,
            verify=verify,
            timeout=(DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        )
        if response.status_code != 200:
            logger.error("AGC AI Networking search failed! status=%s", response.status_code)
            response.raise_for_status()

        payload = response.json()
        code = payload.get("code")
        if code != 0:
            raise RuntimeError(
                f"AGC AI Networking search failed: code={code} msg={payload.get('msg')}"
            )
        return self._parse_results(payload)

    async def aresults(self, query: str) -> list[dict]:
        """Run query through AGC AI Networking webSearch (async, soft-fail)."""
        if not (query or "").strip():
            return []
        url = self._resolved_search_url
        headers = self._build_headers()
        body = self._build_request_body(query)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(
            "TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"]
        )

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
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout, trust_env=True
            ) as session, session.post(
                url=url, headers=headers, json=body
            ) as response:
                if response.status not in (200, 201):
                    logger.error(
                        "AGC AI Networking search failed! status=%s",
                        response.status,
                    )
                    return []
                payload = await response.json()
        except aiohttp.ClientError as e:
            if LogManager.is_sensitive():
                logger.error("AGC AI Networking search request failed!")
            else:
                logger.error("AGC AI Networking search request failed! error=%s", e)
            return []

        code = payload.get("code")
        if code != 0:
            if LogManager.is_sensitive():
                logger.error("AGC AI Networking search failed! code=%s", code)
            else:
                logger.error(
                    "AGC AI Networking search failed! code=%s msg=%s",
                    code,
                    payload.get("msg"),
                )
            return []
        return self._parse_results(payload)

    def _parse_results(self, payload: Any) -> list[dict]:
        """Normalize AGC AI Networking webResult[] into research rows."""
        if not isinstance(payload, dict):
            return []
        items = payload.get("webResult", [])
        if not isinstance(items, list):
            return []

        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            title = str(item.get("title") or url)
            # content 优先，空则回退 chunk 字段，再空则 ""
            content = str(item.get("content") or item.get("chunk") or "")
            row: dict[str, Any] = {
                "title": title[:MAX_SEARCH_CONTENT_LENGTH],
                "url": url[:MAX_URL_LENGTH],
                "content": content[:MAX_SEARCH_CONTENT_LENGTH],
                "source": "agc_ainetworking",
            }
            # publishTime: Unix 秒字符串；"0"/非法/缺失 → 不输出 published 键
            publish_time = item.get("publishTime")
            if publish_time is not None:
                try:
                    ts = int(str(publish_time).strip())
                except (TypeError, ValueError):
                    ts = 0
                if ts > 0:
                    try:
                        row["published"] = (
                            datetime.fromtimestamp(ts, tz=UTC)
                            .date()
                            .isoformat()
                        )
                    except (OverflowError, OSError):
                        # 超大/非法时间戳(如 99999999999999999)会导致
                        # fromtimestamp 抛出 OverflowError/OSError,跳过该条 published
                        logger.warning(
                            "%s skip bad publishTime: %r (type=%s)",
                            "agc_ainetworking",
                            publish_time,
                            type(publish_time).__name__,
                        )
            # siteName: 非空才输出
            site_name = str(item.get("siteName") or "").strip()
            if site_name:
                row["site_name"] = site_name
            results.append(row)
        return results
