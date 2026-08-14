# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
import logging
import re
from typing import Any, Generic, Optional, TypeVar, Union

import httpx
import requests
from pydantic import BaseModel, ConfigDict, SecretStr

from openjiuwen_deepsearch.common.common_constants import (
    MAX_COLLECTOR_DOC_CONTENT_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
    MAX_URL_LENGTH,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    DEFAULT_PUBMED_SEARCH_URL,
    NCBI_REQUEST_CONTROL,
    ScholarlySearchResponseError,
    apply_full_text_extension_config,
    async_request_with_retry,
    ssl_verify,
    sync_request_with_retry,
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


class PubMedSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for PubMed E-utilities search."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = 1
    fetch_full_text: bool = True
    max_full_text_results: int = 1
    full_text_timeout_seconds: int = 30
    max_full_text_length: int = MAX_COLLECTOR_DOC_CONTENT_LENGTH
    requests_per_second: float = 1 / 3
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
        if "pubmed_requests_per_second" in ext:
            try:
                self.requests_per_second = max(0.1, float(ext["pubmed_requests_per_second"]))
            except (TypeError, ValueError):
                pass
        apply_full_text_extension_config(self, ext)

    def results(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        verify = ssl_verify()
        exact_pmid = self._exact_pmid(query)
        ids = [exact_pmid] if exact_pmid else self._search_ids(query, verify)
        if not ids:
            return []
        text = self._get_text(
            f"{self._resolved_search_url()}/efetch.fcgi",
            params=self._fetch_params(ids),
            verify=verify,
        )
        rows = self._parse_fetch_xml(text, ids)
        return self._enrich_rows_sync(rows, verify)

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        verify = ssl_verify()
        async with httpx.AsyncClient(verify=verify, timeout=self.full_text_timeout_seconds) as client:
            exact_pmid = self._exact_pmid(query)
            if exact_pmid:
                ids = [exact_pmid]
            else:
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
            rows = self._parse_fetch_xml(fetch_text, ids)
            return await self._enrich_rows_async(rows, client)

    def _search_ids(self, query: str, verify: Union[str, bool]) -> list[str]:
        raw = self._get_json(
            f"{self._resolved_search_url()}/esearch.fcgi",
            params=self._search_params(query),
            verify=verify,
        )
        return self._parse_ids(raw)

    @staticmethod
    def _exact_pmid(query: str) -> str:
        match = re.fullmatch(r"\s*PMID\s*:\s*(\d+)\s*", str(query or ""), re.IGNORECASE)
        return match.group(1) if match else ""

    async def _aget_json(self, client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> Any:
        response = await self._aget_response(client, url, params)
        raw = response.json()
        self._raise_for_search_error_payload(raw)
        return raw

    async def _aget_text(self, client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> str:
        response = await self._aget_response(client, url, params)
        return response.text

    async def _aget_response(self, client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> Any:
        return await async_request_with_retry(
            lambda: client.get(url, params=params),
            self._wait_for_async_rate_limit,
            control=NCBI_REQUEST_CONTROL,
        )

    async def _wait_for_async_rate_limit(self) -> None:
        await NCBI_REQUEST_CONTROL.wait_async(self.requests_per_second)

    def _wait_for_sync_rate_limit(self) -> None:
        NCBI_REQUEST_CONTROL.wait_sync(self.requests_per_second)

    def _get_json(self, url: str, params: dict[str, Any], verify: Union[str, bool]) -> Any:
        response = self._get_response(url, params, verify)
        raw = response.json()
        self._raise_for_search_error_payload(raw)
        return raw

    def _get_text(self, url: str, params: dict[str, Any], verify: Union[str, bool]) -> str:
        response = self._get_response(url, params, verify)
        return response.text

    def _get_response(self, url: str, params: dict[str, Any], verify: Union[str, bool]) -> Any:
        return sync_request_with_retry(
            lambda: requests.get(
                url,
                params=params,
                verify=verify,
                timeout=self.full_text_timeout_seconds,
            ),
            self._wait_for_sync_rate_limit,
            control=NCBI_REQUEST_CONTROL,
        )

    def _raise_for_search_error_payload(self, raw: Any) -> None:
        message = self._search_error_message(raw)
        if message:
            raise ScholarlySearchResponseError(f"PubMed ESearch returned error: {message}")
        esearch = raw.get("esearchresult") if isinstance(raw, dict) else None
        warning = self._joined_payload_text(esearch.get("errorlist")) if isinstance(esearch, dict) else ""
        if warning:
            logger.info("PubMed ESearch returned nonfatal query warning: %s", warning)

    @classmethod
    def _search_error_message(cls, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""

        message = cls._joined_payload_text(raw.get("error")) or cls._joined_payload_text(raw.get("message"))
        if message:
            return message

        return ""

    @classmethod
    def _joined_payload_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            parts = [cls._joined_payload_text(item) for item in value.values()]
            return "; ".join(part for part in parts if part)
        if isinstance(value, (list, tuple, set)):
            parts = [cls._joined_payload_text(item) for item in value]
            return "; ".join(part for part in parts if part)
        return str(value).strip()

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

    def _pmc_fetch_params(self, pmcid: str) -> dict[str, Any]:
        params = self._fetch_params([pmcid])
        params["db"] = "pmc"
        return params

    def _enrich_rows_sync(self, rows: list[dict[str, Any]], verify: Union[str, bool]) -> list[dict[str, Any]]:
        if not self.fetch_full_text:
            return rows
        limit = max(0, int(self.max_full_text_results))
        for row in rows[:limit]:
            pmcid = str(row.get("pmcid") or "").strip()
            if not pmcid:
                continue
            try:
                text = self._get_text(
                    f"{self._resolved_search_url()}/efetch.fcgi",
                    params=self._pmc_fetch_params(pmcid),
                    verify=verify,
                )
                full_text, truncated = self._parse_pmc_xml(text)
                self._apply_full_text(row, pmcid, full_text, truncated)
            except Exception as exc:
                row.update(_full_text_fields())
                row["full_text_status"] = "failed"
                logger.warning("Unable to enrich PubMed result %s from PMC: %s", row.get("source_id"), exc)
        return rows

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
        pmcid = str(row.get("pmcid") or "").strip()
        if not pmcid:
            return
        try:
            text = await self._aget_text(
                client,
                f"{self._resolved_search_url()}/efetch.fcgi",
                params=self._pmc_fetch_params(pmcid),
            )
            full_text, truncated = self._parse_pmc_xml(text)
            self._apply_full_text(row, pmcid, full_text, truncated)
        except Exception as exc:
            row.update(_full_text_fields())
            row["full_text_status"] = "failed"
            logger.warning("Unable to enrich PubMed result %s from PMC: %s", row.get("source_id"), exc)

    @staticmethod
    def _apply_full_text(row: dict[str, Any], pmcid: str, full_text: str, truncated: bool) -> None:
        if not full_text:
            return
        row.update({
            "full_text": full_text,
            "content_type": "full_text",
            "full_text_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
            "full_text_format": "jats_xml",
            "full_text_status": "available",
            "full_text_truncated": truncated,
        })
    @staticmethod
    def _parse_ids(raw: Any) -> list[str]:
        if not isinstance(raw, dict):
            return []
        ids = raw.get("esearchresult", {}).get("idlist", [])
        return [str(item) for item in ids if item]

    def _parse_fetch_xml(self, text: str, ids: list[str]) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ScholarlySearchResponseError("PubMed EFetch returned invalid XML") from exc
        if root.tag == "ERROR":
            raise ScholarlySearchResponseError(f"PubMed EFetch returned error: {self._joined_text(root)}")
        error = root.find(".//ERROR")
        if error is not None:
            raise ScholarlySearchResponseError(f"PubMed EFetch returned error: {self._joined_text(error)}")
        if root.tag != "PubmedArticleSet":
            raise ScholarlySearchResponseError("PubMed EFetch returned unexpected XML root")

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
            **_full_text_fields(),
        }
        pmcid = self._article_id(article, "pmc")
        if pmcid:
            row["pmcid"] = pmcid
        if doi:
            row["doi"] = doi
        if abstract:
            row["abstract"] = truncate(abstract, MAX_SEARCH_CONTENT_LENGTH)
        return row

    def _parse_pmc_xml(self, text: str) -> tuple[str, bool]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ScholarlySearchResponseError("PMC EFetch returned invalid XML") from exc
        body = root.find(".//body")
        if body is None:
            return "", False

        parts: list[str] = []
        structured_text_descendants: set[ET.Element] = set()
        for element in body.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in {"title", "p"}:
                if element in structured_text_descendants:
                    continue
                value = self._joined_text(element)
                if value:
                    parts.append(value)
            elif tag == "tr":
                cells = [
                    self._joined_text(cell)
                    for cell in list(element)
                    if cell.tag.rsplit("}", 1)[-1] in {"th", "td"}
                ]
                cells = [cell for cell in cells if cell]
                if cells:
                    parts.append(" | ".join(cells))
            elif tag == "table-wrap":
                structured_text_descendants.update(element.iter())
                label = self._joined_text(element.find("label"))
                caption = self._joined_text(element.find("caption"))
                value = " ".join(part for part in (label, caption) if part)
                if value:
                    parts.append(value)
            elif tag == "fig":
                structured_text_descendants.update(element.iter())
                label = self._joined_text(element.find("label"))
                caption = self._joined_text(element.find("caption"))
                value = " ".join(part for part in (label, caption) if part)
                if value:
                    parts.append(value)

        normalized = "\n\n".join(dict.fromkeys(parts)).strip()
        limit = max(0, int(self.max_full_text_length))
        truncated = bool(limit and len(normalized) > limit)
        return (normalized[:limit] if limit else ""), truncated

    def _article_id(self, article: ET.Element, id_type: str) -> str:
        for item in article.findall(".//ArticleId"):
            if str(item.attrib.get("IdType") or "").casefold() == id_type.casefold():
                return self._joined_text(item)
        return ""

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
