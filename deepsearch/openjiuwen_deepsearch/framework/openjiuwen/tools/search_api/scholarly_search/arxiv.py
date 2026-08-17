# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Generic, Optional, TypeVar
from urllib.parse import quote_plus

import httpx
import pypdfium2 as pdfium
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import (
    MAX_COLLECTOR_DOC_CONTENT_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
    MAX_URL_LENGTH,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    ATOM_NAMESPACE,
    ARXIV_DOWNLOAD_LIMITER,
    ARXIV_REQUEST_CONTROL,
    DEFAULT_ARXIV_SEARCH_URL,
    ScholarlySearchResponseError,
    apply_full_text_extension_config,
    async_request_once,
    http_status_code,
    is_transient_connection_error,
    ssl_verify,
    sync_request_once,
    truncate,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


def _full_text_fields() -> dict[str, Any]:
    return {
        "skip_webpage_enrichment": True,
        "full_text": "",
        "content_type": "abstract",
        "full_text_url": "",
        "full_text_format": "",
        "full_text_status": "unavailable",
        "full_text_truncated": False,
    }


class ArxivSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for the arXiv Atom API."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 1
    fetch_full_text: bool = True
    max_full_text_results: int = 1
    full_text_timeout_seconds: int = 30
    max_full_text_length: int = MAX_COLLECTOR_DOC_CONTENT_LENGTH
    min_full_text_length: int = 200
    requests_per_second: float = 1 / 3
    extension: Optional[dict] = None

    sort_by: str = "relevance"
    sort_order: str = "descending"

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def model_post_init(self, __context: Any) -> None:
        ext = self.extension or {}
        if "arxiv_sort_by" in ext:
            self.sort_by = ext["arxiv_sort_by"]
        if "arxiv_sort_order" in ext:
            self.sort_order = ext["arxiv_sort_order"]
        if "arxiv_requests_per_second" in ext:
            try:
                self.requests_per_second = max(0.1, float(ext["arxiv_requests_per_second"]))
            except (TypeError, ValueError):
                pass
        apply_full_text_extension_config(self, ext)

    def results(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        text = self._get_text(self._build_url(query))
        return self._enrich_rows_sync(self._parse_atom(text))

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        async with httpx.AsyncClient(
            verify=ssl_verify(),
            timeout=self.full_text_timeout_seconds,
            follow_redirects=True,
        ) as client:
            text = await self._aget_text(client, self._build_url(query))
            return await self._enrich_rows_async(self._parse_atom(text), client)

    async def _aget_text(self, client: httpx.AsyncClient, url: str) -> str:
        response = await self._aget_response(client, url, rate_limit=self._is_search_api_url(url))
        return response.text

    def _get_text(self, url: str) -> str:
        response = self._get_response(url, rate_limit=self._is_search_api_url(url))
        return response.text

    async def _aget_bytes(self, client: httpx.AsyncClient, url: str) -> bytes:
        response = await self._aget_response(client, url)
        return response.content

    def _get_bytes(self, url: str) -> bytes:
        response = self._get_response(url)
        return response.content

    async def _aget_response(self, client: httpx.AsyncClient, url: str, *, rate_limit: bool = False) -> Any:
        before_attempt = (
            self._wait_for_async_rate_limit
            if rate_limit
            else lambda: ARXIV_REQUEST_CONTROL.wait_async()
        )
        if rate_limit:
            return await async_request_once(
                lambda: client.get(url),
                before_attempt,
                control=ARXIV_REQUEST_CONTROL,
            )
        async with ARXIV_DOWNLOAD_LIMITER.async_slot():
            return await async_request_once(
                lambda: client.get(url),
                before_attempt,
                control=ARXIV_REQUEST_CONTROL,
            )

    def _get_response(self, url: str, *, rate_limit: bool = False) -> Any:
        def request() -> Any:
            return requests.get(
                url,
                verify=ssl_verify(),
                timeout=self.full_text_timeout_seconds,
                allow_redirects=True,
            )

        before_attempt = (
            self._wait_for_sync_rate_limit
            if rate_limit
            else lambda: ARXIV_REQUEST_CONTROL.wait_sync()
        )
        if rate_limit:
            return sync_request_once(
                request,
                before_attempt,
                control=ARXIV_REQUEST_CONTROL,
            )
        with ARXIV_DOWNLOAD_LIMITER.sync_slot():
            return sync_request_once(
                request,
                before_attempt,
                control=ARXIV_REQUEST_CONTROL,
            )

    async def _wait_for_async_rate_limit(self) -> None:
        await ARXIV_REQUEST_CONTROL.wait_async(self.requests_per_second)

    def _wait_for_sync_rate_limit(self) -> None:
        ARXIV_REQUEST_CONTROL.wait_sync(self.requests_per_second)

    def _is_search_api_url(self, url: str) -> bool:
        return str(url).startswith(self._resolved_search_url())

    def _build_url(self, query: str) -> str:
        return (
            f"{self._resolved_search_url()}?search_query=all:{quote_plus(query)}"
            f"&start=0&max_results={self.max_web_search_results}"
            f"&sortBy={quote_plus(self.sort_by)}&sortOrder={quote_plus(self.sort_order)}"
        )

    def _parse_atom(self, text: str) -> list[dict[str, Any]]:
        ns = {"atom": ATOM_NAMESPACE}
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ScholarlySearchResponseError("arXiv API returned invalid XML") from exc
        if root.tag != f"{{{ATOM_NAMESPACE}}}feed":
            raise ScholarlySearchResponseError("arXiv API returned non-Atom feed response")
        rows: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", ns):
            arxiv_id = truncate(entry.findtext("atom:id", default="", namespaces=ns), MAX_URL_LENGTH)
            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
            summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split())
            if self._is_error_entry(arxiv_id, title):
                raise ScholarlySearchResponseError(
                    f"arXiv API returned error: {summary or arxiv_id}"
                )
            published = truncate(entry.findtext("atom:published", default="", namespaces=ns), 64)
            authors = [
                name.text.strip()
                for name in entry.findall("atom:author/atom:name", ns)
                if name.text and name.text.strip()
            ]
            categories = [
                category.attrib.get("term", "")
                for category in entry.findall("atom:category", ns)
                if category.attrib.get("term")
            ]
            rows.append(
                {
                    "title": truncate(title or arxiv_id, MAX_SEARCH_CONTENT_LENGTH),
                    "url": arxiv_id[:MAX_URL_LENGTH],
                    "content": truncate(summary, MAX_SEARCH_CONTENT_LENGTH),
                    "source": "arxiv",
                    "source_id": self._source_id(arxiv_id),
                    "published": published,
                    "authors": authors,
                    "categories": categories,
                    **_full_text_fields(),
                }
            )
        return rows

    def _parse_arxiv_html(self, html: str) -> tuple[str, bool]:
        soup = BeautifulSoup(str(html or ""), "html.parser")
        for element in soup.select("script, style, nav, header, footer, aside"):
            element.decompose()
        article = soup.find("article") or soup.find("main")
        if article is None:
            return "", False
        text = "\n\n".join(article.stripped_strings)
        limit = max(0, int(self.max_full_text_length))
        truncated = bool(limit and len(text) > limit)
        return (text[:limit] if limit else ""), truncated

    def _extract_pdf_text(self, data: bytes) -> tuple[str, bool]:
        document = pdfium.PdfDocument(data)
        try:
            limit = max(0, int(self.max_full_text_length))
            if not limit:
                return "", False
            parts: list[str] = []
            page_count = len(document)
            for page_index, page in enumerate(document):
                text_page = page.get_textpage()
                try:
                    value = " ".join(text_page.get_text_range().split())
                    if value:
                        parts.append(value)
                finally:
                    text_page.close()
                    page.close()
                text = "\n\n".join(parts)
                if len(text) >= limit:
                    truncated = len(text) > limit or page_index + 1 < page_count
                    return text[:limit], truncated
        finally:
            document.close()
        return "\n\n".join(parts), False

    def _enrich_rows_sync(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.fetch_full_text:
            return rows
        limit = max(0, int(self.max_full_text_results))
        for row in rows[:limit]:
            self._enrich_one_sync(row)
        return rows

    def _enrich_one_sync(self, row: dict[str, Any]) -> None:
        arxiv_id = str(row.get("source_id") or "").strip()
        if not arxiv_id:
            return
        html_url = f"https://arxiv.org/html/{arxiv_id}"
        try:
            html = self._get_text(html_url)
            text, truncated = self._parse_arxiv_html(html)
            if len(text) >= max(0, int(self.min_full_text_length)):
                self._apply_full_text(row, text, html_url, "html", truncated)
                return
        except Exception as exc:
            logger.debug("Unable to enrich arXiv result %s from HTML: %s", arxiv_id, exc)
        last_error: Exception | None = None
        for pdf_index, pdf_url in enumerate(self._pdf_urls(arxiv_id)):
            try:
                text, truncated = self._extract_pdf_text(self._get_bytes(pdf_url))
                if len(text) < max(0, int(self.min_full_text_length)):
                    raise ScholarlySearchResponseError("arXiv PDF text is insufficient")
                self._apply_full_text(row, text, pdf_url, "pdf", truncated)
                return
            except Exception as exc:
                last_error = exc
                if pdf_index == 0 and self._should_try_alternate_pdf(exc):
                    continue
                break
        row.update(_full_text_fields())
        row["full_text_status"] = "failed"
        logger.warning("Unable to enrich arXiv result %s: %r", arxiv_id, last_error)

    async def _enrich_rows_async(
        self,
        rows: list[dict[str, Any]],
        client: httpx.AsyncClient,
    ) -> list[dict[str, Any]]:
        if not self.fetch_full_text:
            return rows
        limit = max(0, int(self.max_full_text_results))
        await asyncio.gather(*(self._enrich_one_async(row, client) for row in rows[:limit]))
        return rows

    async def _enrich_one_async(self, row: dict[str, Any], client: httpx.AsyncClient) -> None:
        arxiv_id = str(row.get("source_id") or "").strip()
        if not arxiv_id:
            return
        html_url = f"https://arxiv.org/html/{arxiv_id}"
        try:
            text, truncated = self._parse_arxiv_html(await self._aget_text(client, html_url))
            if len(text) >= max(0, int(self.min_full_text_length)):
                self._apply_full_text(row, text, html_url, "html", truncated)
                return
        except Exception as exc:
            logger.debug("Unable to enrich arXiv result %s from HTML: %s", arxiv_id, exc)
        last_error: Exception | None = None
        for pdf_index, pdf_url in enumerate(self._pdf_urls(arxiv_id)):
            try:
                pdf_data = await self._aget_bytes(client, pdf_url)
                text, truncated = await asyncio.to_thread(self._extract_pdf_text, pdf_data)
                if len(text) < max(0, int(self.min_full_text_length)):
                    raise ScholarlySearchResponseError("arXiv PDF text is insufficient")
                self._apply_full_text(row, text, pdf_url, "pdf", truncated)
                return
            except Exception as exc:
                last_error = exc
                if pdf_index == 0 and self._should_try_alternate_pdf(exc):
                    continue
                break
        row.update(_full_text_fields())
        row["full_text_status"] = "failed"
        logger.warning("Unable to enrich arXiv result %s: %r", arxiv_id, last_error)

    @staticmethod
    def _source_id(entry_url: str) -> str:
        marker = "/abs/"
        return entry_url.split(marker, 1)[1] if marker in entry_url else entry_url.rsplit("/", 1)[-1]

    @staticmethod
    def _pdf_urls(arxiv_id: str) -> tuple[str, str]:
        base = f"https://arxiv.org/pdf/{arxiv_id}"
        return base, f"{base}.pdf"

    @staticmethod
    def _should_try_alternate_pdf(error: BaseException) -> bool:
        status = http_status_code(error)
        if status is not None:
            return status == 404
        return not is_transient_connection_error(error)

    @staticmethod
    def _apply_full_text(
        row: dict[str, Any],
        text: str,
        url: str,
        full_text_format: str,
        truncated: bool,
    ) -> None:
        row.update({
            "full_text": text,
            "content_type": "full_text",
            "full_text_url": url,
            "full_text_format": full_text_format,
            "full_text_status": "available",
            "full_text_truncated": truncated,
        })

    @staticmethod
    def _is_error_entry(arxiv_id: str, title: str) -> bool:
        return title.strip().casefold() == "error" and "/api/errors#" in arxiv_id

    def _resolved_search_url(self) -> str:
        configured = ""
        if self.search_url is not None:
            configured = (
                self.search_url.get_secret_value()
                if hasattr(self.search_url, "get_secret_value")
                else str(self.search_url)
            )
        configured = (configured or "").strip().rstrip("/")
        return configured or DEFAULT_ARXIV_SEARCH_URL
