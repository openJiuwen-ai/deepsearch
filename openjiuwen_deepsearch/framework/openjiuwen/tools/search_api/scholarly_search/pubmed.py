# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from typing import Any, Generic, Optional, TypeVar, Union

import httpx
import requests
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH, MAX_URL_LENGTH
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    DEFAULT_PUBMED_SEARCH_URL,
    pubmed_rate_limiter,
    retry_delay_seconds,
    ssl_verify,
    truncate,
)

T = TypeVar("T")


class PubMedSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for PubMed E-utilities search."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 5
    extension: Optional[dict] = None

    email: str | None = None
    tool: str = "openjiuwen-deepsearch"
    rate_limit_max_attempts: int = 3
    rate_limit_backoff_base_seconds: float = 1.0

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
        verify = ssl_verify()
        ids = self._search_ids(query, verify)
        if not ids:
            return []
        text = self._get_text(
            f"{self._resolved_search_url()}/efetch.fcgi",
            params=self._fetch_params(ids),
            verify=verify,
        )
        return self._parse_fetch_xml(text, ids)

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        verify = ssl_verify()
        async with httpx.AsyncClient(verify=verify, timeout=30) as client:
            search_raw = await self._aget_json(
                client,
                f"{self._resolved_search_url()}/esearch.fcgi",
                params=self._search_params(query),
            )
            ids = self._parse_ids(search_raw)
            if not ids:
                return []
            fetch_text = await self._aget_text(
                client,
                f"{self._resolved_search_url()}/efetch.fcgi",
                params=self._fetch_params(ids),
            )
            return self._parse_fetch_xml(fetch_text, ids)

    def _search_ids(self, query: str, verify: Union[str, bool]) -> list[str]:
        raw = self._get_json(
            f"{self._resolved_search_url()}/esearch.fcgi",
            params=self._search_params(query),
            verify=verify,
        )
        return self._parse_ids(raw)

    async def _aget_json(self, client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> Any:
        last_response = None
        for attempt in range(self.rate_limit_max_attempts):
            await self._aacquire_rate_limit()
            response = await client.get(url, params=params)
            last_response = response
            if response.status_code == 429:
                await asyncio.sleep(self._retry_delay(attempt, response.headers))
                continue
            response.raise_for_status()
            raw = response.json()
            if self._is_rate_limited_payload(raw):
                await asyncio.sleep(self._retry_delay(attempt, None))
                continue
            return raw
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("PubMed E-utilities rate limit exceeded")

    async def _aget_text(self, client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> str:
        last_response = None
        for attempt in range(self.rate_limit_max_attempts):
            await self._aacquire_rate_limit()
            response = await client.get(url, params=params)
            last_response = response
            if response.status_code == 429:
                await asyncio.sleep(self._retry_delay(attempt, response.headers))
                continue
            response.raise_for_status()
            return response.text
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("PubMed E-utilities rate limit exceeded")

    def _get_json(self, url: str, params: dict[str, Any], verify: Union[str, bool]) -> Any:
        last_response = None
        for attempt in range(self.rate_limit_max_attempts):
            self._acquire_rate_limit()
            response = requests.get(url, params=params, verify=verify, timeout=30)
            last_response = response
            if response.status_code == 429:
                time.sleep(self._retry_delay(attempt, response.headers))
                continue
            response.raise_for_status()
            raw = response.json()
            if self._is_rate_limited_payload(raw):
                time.sleep(self._retry_delay(attempt, None))
                continue
            return raw
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("PubMed E-utilities rate limit exceeded")

    def _get_text(self, url: str, params: dict[str, Any], verify: Union[str, bool]) -> str:
        last_response = None
        for attempt in range(self.rate_limit_max_attempts):
            self._acquire_rate_limit()
            response = requests.get(url, params=params, verify=verify, timeout=30)
            last_response = response
            if response.status_code == 429:
                time.sleep(self._retry_delay(attempt, response.headers))
                continue
            response.raise_for_status()
            return response.text
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("PubMed E-utilities rate limit exceeded")

    def _acquire_rate_limit(self) -> None:
        pubmed_rate_limiter(bool(self._api_key_to_str())).acquire()

    async def _aacquire_rate_limit(self) -> None:
        await pubmed_rate_limiter(bool(self._api_key_to_str())).aacquire()

    def _retry_delay(self, attempt: int, headers: Any) -> float:
        return retry_delay_seconds(attempt, headers, base_delay=self.rate_limit_backoff_base_seconds)

    @staticmethod
    def _is_rate_limited_payload(raw: Any) -> bool:
        if not isinstance(raw, dict):
            return False
        message = " ".join(str(raw.get(key) or "") for key in ("error", "message")).lower()
        return "rate limit" in message or "too many requests" in message

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

    def _fetch_params(self, ids: list[str]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "xml",
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

    def _parse_fetch_xml(self, text: str, ids: list[str]) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        rows_by_pmid: dict[str, dict[str, Any]] = {}
        for article in root.findall(".//PubmedArticle"):
            item = self._parse_pubmed_article(article)
            pmid = item.get("source_id")
            if pmid:
                rows_by_pmid[pmid] = item

        rows: list[dict[str, Any]] = []
        for pmid in ids:
            if pmid in rows_by_pmid:
                rows.append(rows_by_pmid[pmid])
        return rows

    def _parse_pubmed_article(self, article: ET.Element) -> dict[str, Any]:
        pmid = self._text(article, ".//MedlineCitation/PMID")
        title = self._joined_text(article.find(".//ArticleTitle")) or f"PubMed PMID {pmid}"
        journal = self._text(article, ".//Journal/Title") or self._text(article, ".//Journal/ISOAbbreviation")
        published = self._publication_date(article)
        authors = self._authors(article)
        abstract = self._abstract(article)
        publication_types = [
            self._joined_text(item)
            for item in article.findall(".//PublicationTypeList/PublicationType")
            if self._joined_text(item)
        ]
        doi = self._doi(article)
        bibliographic_parts = [part for part in (journal, published, "; ".join(authors[:5])) if part]
        content = abstract or " | ".join(bibliographic_parts) or title

        row = {
            "title": truncate(title, MAX_SEARCH_CONTENT_LENGTH),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"[:MAX_URL_LENGTH],
            "content": truncate(content, MAX_SEARCH_CONTENT_LENGTH),
            "source": "pubmed",
            "source_id": pmid,
            "published": truncate(published, 64),
            "authors": authors,
            "journal": truncate(journal, MAX_SEARCH_CONTENT_LENGTH),
            "publication_types": publication_types,
        }
        if doi:
            row["doi"] = doi
        if abstract:
            row["abstract"] = truncate(abstract, MAX_SEARCH_CONTENT_LENGTH)
        return row

    def _abstract(self, article: ET.Element) -> str:
        parts = []
        for item in article.findall(".//Abstract/AbstractText"):
            text = self._joined_text(item)
            if not text:
                continue
            label = item.attrib.get("Label") or item.attrib.get("NlmCategory") or ""
            parts.append(f"{label}: {text}" if label else text)
        return " ".join(parts)

    def _authors(self, article: ET.Element) -> list[str]:
        authors = []
        for author in article.findall(".//AuthorList/Author"):
            collective = self._text(author, "CollectiveName")
            if collective:
                authors.append(collective)
                continue
            last_name = self._text(author, "LastName")
            fore_name = self._text(author, "ForeName") or self._text(author, "Initials")
            name = " ".join(part for part in (fore_name, last_name) if part)
            if name:
                authors.append(name)
        return authors

    def _publication_date(self, article: ET.Element) -> str:
        pub_date = article.find(".//JournalIssue/PubDate")
        if pub_date is None:
            return ""
        year = self._text(pub_date, "Year")
        month = self._text(pub_date, "Month")
        day = self._text(pub_date, "Day")
        medline_date = self._text(pub_date, "MedlineDate")
        return " ".join(part for part in (year, month, day) if part) or medline_date

    def _doi(self, article: ET.Element) -> str:
        for item in article.findall(".//ELocationID"):
            if item.attrib.get("EIdType") == "doi":
                return self._joined_text(item)
        for item in article.findall(".//ArticleId"):
            if item.attrib.get("IdType") == "doi":
                return self._joined_text(item)
        return ""

    @staticmethod
    def _text(element: ET.Element, path: str) -> str:
        found = element.find(path)
        if found is None or found.text is None:
            return ""
        return found.text.strip()

    @staticmethod
    def _joined_text(element: ET.Element | None) -> str:
        if element is None:
            return ""
        return " ".join("".join(element.itertext()).split())

    def _resolved_search_url(self) -> str:
        configured = ""
        if self.search_url is not None:
            configured = (
                self.search_url.get_secret_value()
                if hasattr(self.search_url, "get_secret_value")
                else str(self.search_url)
            )
        configured = (configured or "").strip().rstrip("/")
        return configured or DEFAULT_PUBMED_SEARCH_URL

    def _api_key_to_str(self) -> str:
        value = self.search_api_key
        if isinstance(value, bytearray):
            return value.decode("utf-8")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")
