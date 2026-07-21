from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

import requests
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

DEFAULT_JINA_READER_BASE_URLS: tuple[str, ...] = (
    "https://r.jinaai.cn",
    "https://r.jina.ai",
)


def _parse_jina_reader_base_url_override(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [part.strip().rstrip("/") for part in str(raw).split(",") if part.strip()]


def _dedupe_urls_preserve_order(urls: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return tuple(ordered)


def resolve_jina_reader_base_urls(configured_base_url: str = "") -> tuple[str, ...]:
    """Reader bases for Jina fetch: explicit config first, then env override, then defaults."""
    configured = [configured_base_url.strip().rstrip("/")] if configured_base_url.strip() else []
    override = _parse_jina_reader_base_url_override(os.getenv("JINA_READER_BASE_URL"))
    return _dedupe_urls_preserve_order((*configured, *override, *DEFAULT_JINA_READER_BASE_URLS))


def build_jina_reader_url(base_url: str, target_url: str) -> str:
    return f"{base_url.rstrip('/')}/{target_url}"


def jina_reader_request_timeout() -> tuple[float, float]:
    connect = float(os.getenv("JINA_READER_CONNECT_TIMEOUT", "4"))
    read = float(os.getenv("JINA_READER_READ_TIMEOUT", "8"))
    return connect, read


def _jina_reader_auth_failure(response: requests.Response) -> bool:
    if response.status_code in (401, 403):
        return True
    if response.status_code < 500:
        return False
    body = (response.text or "").lower()
    return "authenticate" in body or "authenticationrequired" in body


class JinaWebFetchProvider:
    provider_name = "jina"

    def __init__(
        self,
        *,
        api_key: bytearray | bytes | str | None = None,
        base_url: str = "",
        extension: Optional[dict] = None,
    ) -> None:
        del extension
        self.api_key = self._api_key_to_str(api_key)
        self._reader_bases = resolve_jina_reader_base_urls(base_url)
        self._reader_timeout = jina_reader_request_timeout()

    def fetch_page(self, url: str) -> str:
        for _ in range(2):
            content = self._read_via_jina(url)
            if not content:
                continue
            is_not_failed = not content.startswith("[web_fetch] Failed")
            is_not_empty = content != "[web_fetch] Empty content."
            is_not_parser_error = not content.startswith("[document_parser]")
            if is_not_failed and is_not_empty and is_not_parser_error:
                return content
        return "[web_fetch] Failed to read page."

    def _read_via_jina(self, url: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        if not self._reader_bases:
            return "[web_fetch] Failed to read page."

        def _fetch_base(base: str) -> tuple[str, requests.Response | None, RequestException | None]:
            reader_url = build_jina_reader_url(base, url)
            try:
                resp = requests.get(
                    reader_url,
                    headers=headers,
                    timeout=self._reader_timeout,
                )
                return base, resp, None
            except RequestException as exc:
                return base, None, exc

        last_error: RequestException | None = None

        with ThreadPoolExecutor(max_workers=len(self._reader_bases)) as pool:
            futures = [pool.submit(_fetch_base, base) for base in self._reader_bases]
            for future in as_completed(futures):
                base, resp, err = future.result()
                if err is not None:
                    last_error = err
                    logger.warning(
                        "[WebFetch] Jina reader %s unreachable: %s",
                        base,
                        err,
                    )
                    continue
                if resp.status_code == 200:
                    return resp.text
                if _jina_reader_auth_failure(resp):
                    logger.warning(
                        "[WebFetch] Jina reader %s rejected credentials for target url",
                        base,
                    )
                    return "[web_fetch] Failed to read page."
                logger.warning(
                    "[WebFetch] Jina reader %s returned HTTP %s for target url",
                    base,
                    resp.status_code,
                )

        if last_error is not None:
            logger.warning(
                "[WebFetch] all Jina reader endpoints failed for target url: %s",
                last_error,
                exc_info=True,
            )
        return "[web_fetch] Failed to read page."

    @staticmethod
    def _api_key_to_str(value: bytearray | bytes | str | None) -> str:
        if isinstance(value, bytearray):
            return value.decode("utf-8")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")
