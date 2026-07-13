# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Generic, Optional, TypeVar, Union
from urllib.parse import quote_plus

import httpx
import requests
from openjiuwen.core.common.security.ssl_utils import SslUtils
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH, MAX_URL_LENGTH

T = TypeVar("T")

DEFAULT_PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"


def _truncate(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _ssl_verify() -> Union[str, bool]:
    ssl_verify, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
    return ssl_cert if ssl_verify else False


class PubMedSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for PubMed E-utilities search."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    email: str | None = None
    tool: str = "openjiuwen-deepsearch"

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def model_post_init(self, __context: Any) -> None:
        ext = self.extension or {}
        if "pubmed_email" in ext:
            self.email = ext["pubmed_email"]
        if "pubmed_tool" in ext:
            self.tool = ext["pubmed_tool"]

    def results(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        verify = _ssl_verify()
        ids = self._search_ids(query, verify)
        if not ids:
            return []
        response = requests.get(
            f"{self._resolved_search_url()}/esummary.fcgi",
            params=self._summary_params(ids),
            verify=verify,
            timeout=30,
        )
        response.raise_for_status()
        return self._parse_summary(response.json(), ids)

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        verify = _ssl_verify()
        async with httpx.AsyncClient(verify=verify, timeout=30) as client:
            search_response = await client.get(
                f"{self._resolved_search_url()}/esearch.fcgi",
                params=self._search_params(query),
            )
            search_response.raise_for_status()
            ids = self._parse_ids(search_response.json())
            if not ids:
                return []
            summary_response = await client.get(
                f"{self._resolved_search_url()}/esummary.fcgi",
                params=self._summary_params(ids),
            )
            summary_response.raise_for_status()
            return self._parse_summary(summary_response.json(), ids)

    def _search_ids(self, query: str, verify: Union[str, bool]) -> list[str]:
        response = requests.get(
            f"{self._resolved_search_url()}/esearch.fcgi",
            params=self._search_params(query),
            verify=verify,
            timeout=30,
        )
        response.raise_for_status()
        return self._parse_ids(response.json())

    def _search_params(self, query: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": self.max_web_search_results,
            "tool": self.tool,
        }
        api_key = self._api_key_to_str()
        if api_key:
            params["api_key"] = api_key
        if self.email:
            params["email"] = self.email
        return params

    def _summary_params(self, ids: list[str]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
            "tool": self.tool,
        }
        api_key = self._api_key_to_str()
        if api_key:
            params["api_key"] = api_key
        if self.email:
            params["email"] = self.email
        return params

    @staticmethod
    def _parse_ids(raw: Any) -> list[str]:
        if not isinstance(raw, dict):
            return []
        ids = raw.get("esearchresult", {}).get("idlist", [])
        return [str(item) for item in ids if item]

    def _parse_summary(self, raw: Any, ids: list[str]) -> list[dict[str, Any]]:
        result = raw.get("result", {}) if isinstance(raw, dict) else {}
        rows: list[dict[str, Any]] = []
        for pmid in ids:
            item = result.get(pmid, {})
            if not isinstance(item, dict):
                continue
            title = _truncate(item.get("title") or f"PubMed PMID {pmid}", MAX_SEARCH_CONTENT_LENGTH)
            journal = _truncate(item.get("fulljournalname") or item.get("source"), MAX_SEARCH_CONTENT_LENGTH)
            published = _truncate(item.get("pubdate") or item.get("epubdate"), 64)
            authors = [
                author.get("name")
                for author in item.get("authors", [])
                if isinstance(author, dict) and author.get("name")
            ]
            content_parts = [part for part in (journal, published, "; ".join(authors[:5])) if part]
            rows.append(
                {
                    "title": title,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"[:MAX_URL_LENGTH],
                    "content": _truncate(" | ".join(content_parts) or title, MAX_SEARCH_CONTENT_LENGTH),
                    "source": "pubmed",
                    "source_id": pmid,
                    "published": published,
                    "authors": authors,
                    "journal": journal,
                }
            )
        return rows

    def _resolved_search_url(self) -> str:
        configured = ""
        if self.search_url is not None:
            configured = self.search_url.get_secret_value() if hasattr(self.search_url, "get_secret_value") else str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        return configured or DEFAULT_PUBMED_SEARCH_URL

    def _api_key_to_str(self) -> str:
        value = self.search_api_key
        if isinstance(value, bytearray):
            return value.decode("utf-8")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")


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
        response = requests.get(self._build_url(query), verify=_ssl_verify(), timeout=30)
        response.raise_for_status()
        return self._parse_atom(response.text)

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        async with httpx.AsyncClient(verify=_ssl_verify(), timeout=30) as client:
            response = await client.get(self._build_url(query))
            response.raise_for_status()
            return self._parse_atom(response.text)

    def _build_url(self, query: str) -> str:
        return (
            f"{self._resolved_search_url()}?search_query=all:{quote_plus(query)}"
            f"&start=0&max_results={self.max_web_search_results}"
            f"&sortBy={quote_plus(self.sort_by)}&sortOrder={quote_plus(self.sort_order)}"
        )

    def _parse_atom(self, text: str) -> list[dict[str, Any]]:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(text)
        rows: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", ns):
            arxiv_id = _truncate(entry.findtext("atom:id", default="", namespaces=ns), MAX_URL_LENGTH)
            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
            summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split())
            published = _truncate(entry.findtext("atom:published", default="", namespaces=ns), 64)
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
                    "title": _truncate(title or arxiv_id, MAX_SEARCH_CONTENT_LENGTH),
                    "url": arxiv_id[:MAX_URL_LENGTH],
                    "content": _truncate(summary, MAX_SEARCH_CONTENT_LENGTH),
                    "source": "arxiv",
                    "source_id": arxiv_id.rsplit("/", 1)[-1],
                    "published": published,
                    "authors": authors,
                    "categories": categories,
                }
            )
        return rows

    def _resolved_search_url(self) -> str:
        configured = ""
        if self.search_url is not None:
            configured = self.search_url.get_secret_value() if hasattr(self.search_url, "get_secret_value") else str(self.search_url)
        configured = (configured or "").strip().rstrip("/")
        return configured or DEFAULT_ARXIV_SEARCH_URL
