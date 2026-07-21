# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Generic, Optional, TypeVar
from urllib.parse import quote_plus

import httpx
import requests
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH, MAX_URL_LENGTH
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    ATOM_NAMESPACE,
    DEFAULT_ARXIV_SEARCH_URL,
    ScholarlySearchResponseError,
    ssl_verify,
    truncate,
)

T = TypeVar("T")


class ArxivSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for the arXiv Atom API."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 5
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

    def results(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        text = self._get_text(self._build_url(query))
        return self._parse_atom(text)

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        async with httpx.AsyncClient(verify=ssl_verify(), timeout=30) as client:
            text = await self._aget_text(client, self._build_url(query))
            return self._parse_atom(text)

    async def _aget_text(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    def _get_text(self, url: str) -> str:
        response = requests.get(url, verify=ssl_verify(), timeout=30)
        response.raise_for_status()
        return response.text

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
                    "source_id": arxiv_id.rsplit("/", 1)[-1],
                    "published": published,
                    "authors": authors,
                    "categories": categories,
                }
            )
        return rows

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
