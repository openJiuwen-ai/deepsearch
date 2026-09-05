# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar
from urllib.parse import urlsplit

import httpx
import requests
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openjiuwen_deepsearch.common.common_constants import (
    MAX_COLLECTOR_DOC_CONTENT_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
    MAX_URL_LENGTH,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    DEFAULT_MAX_FULL_TEXT_DOWNLOAD_BYTES,
    ServiceConcurrencyLimiter,
    ServiceRequestControl,
    ScholarlySearchResponseError,
    async_request_once,
    empty_full_text_fields,
    ssl_verify,
    sync_request_once,
    truncate,
)
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_search_service_url

T = TypeVar("T")

DEFAULT_SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SEMANTIC_SCHOLAR_FIELDS = ",".join((
    "paperId", "title", "abstract", "authors", "publicationDate", "year",
    "venue", "journal", "externalIds", "citationCount", "url", "openAccessPdf",
))
SEMANTIC_SCHOLAR_REQUEST_CONTROL = ServiceRequestControl()
SEMANTIC_SCHOLAR_CONCURRENCY_LIMITER = ServiceConcurrencyLimiter(1)


class SemanticScholarSearchAPIWrapper(BaseModel, Generic[T]):
    """Wrapper for Semantic Scholar Academic Graph paper search."""

    search_api_key: bytearray | bytes | str | None = None
    search_url: SecretStr | str | None = None
    max_web_search_results: int = Field(default=1, ge=1, le=10)
    fetch_full_text: bool = True
    max_full_text_results: int = 1
    full_text_timeout_seconds: int = 30
    max_full_text_length: int = MAX_COLLECTOR_DOC_CONTENT_LENGTH
    max_full_text_download_bytes: int = DEFAULT_MAX_FULL_TEXT_DOWNLOAD_BYTES
    extension: Optional[dict] = None
    requests_per_second: float = Field(default=0.5, gt=0)

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def results(self, query: str) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        error = None
        try:
            with SEMANTIC_SCHOLAR_CONCURRENCY_LIMITER.sync_slot():
                response = sync_request_once(
                    lambda: requests.get(
                        self._url(), params=self._params(query), headers=self._headers(),
                        verify=ssl_verify(), timeout=self.full_text_timeout_seconds,
                    ),
                    lambda: SEMANTIC_SCHOLAR_REQUEST_CONTROL.wait_sync(self.requests_per_second),
                    control=SEMANTIC_SCHOLAR_REQUEST_CONTROL,
                )
        except requests.RequestException as exc:
            error = self._sanitized_request_error(exc)
        if error is not None:
            raise error from None
        return self._parse_response(self._response_json(response))[:self.max_web_search_results]

    async def aresults(self, query: str) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        error = None
        try:
            async with httpx.AsyncClient(verify=ssl_verify(), timeout=self.full_text_timeout_seconds) as client:
                async with SEMANTIC_SCHOLAR_CONCURRENCY_LIMITER.async_slot():
                    response = await async_request_once(
                        lambda: client.get(
                            self._url(), params=self._params(query), headers=self._headers()
                        ),
                        lambda: SEMANTIC_SCHOLAR_REQUEST_CONTROL.wait_async(self.requests_per_second),
                        control=SEMANTIC_SCHOLAR_REQUEST_CONTROL,
                    )
        except httpx.HTTPError as exc:
            error = self._sanitized_request_error(exc)
        if error is not None:
            raise error from None
        return self._parse_response(self._response_json(response))[:self.max_web_search_results]

    @staticmethod
    def _sanitized_request_error(error: BaseException) -> ScholarlySearchResponseError:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if isinstance(status, int):
            return ScholarlySearchResponseError(f"Semantic Scholar request failed (HTTP {status})")
        return ScholarlySearchResponseError("Semantic Scholar request failed (transport error)")

    @staticmethod
    def _response_json(response: Any) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ScholarlySearchResponseError("Semantic Scholar API returned invalid JSON") from exc

    def _url(self) -> str:
        value = self.search_url
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        resolved = (str(value).strip() if value else DEFAULT_SEMANTIC_SCHOLAR_SEARCH_URL).rstrip("/")
        if resolved != DEFAULT_SEMANTIC_SCHOLAR_SEARCH_URL:
            validate_search_service_url(resolved)
        return resolved

    def _headers(self) -> dict[str, str]:
        key = self.search_api_key
        if isinstance(key, (bytes, bytearray)):
            key = bytes(key).decode("utf-8", errors="replace")
        return {"x-api-key": str(key).strip()} if key is not None and str(key).strip() else {}

    def _params(self, query: str) -> dict[str, Any]:
        return {"query": query, "limit": self.max_web_search_results, "offset": 0, "fields": SEMANTIC_SCHOLAR_FIELDS}

    @classmethod
    def _parse_response(cls, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ScholarlySearchResponseError("Semantic Scholar API returned malformed data")
        rows = []
        for paper in payload["data"]:
            try:
                row = cls._normalize_paper(paper)
            except (AttributeError, TypeError, ValueError):
                continue
            if row:
                rows.append(row)
        return rows

    @classmethod
    def _normalize_paper(cls, paper: Any) -> dict[str, Any] | None:
        if not isinstance(paper, dict):
            return None
        source_id = truncate(paper.get("paperId"), MAX_URL_LENGTH)
        title = truncate(paper.get("title"), MAX_SEARCH_CONTENT_LENGTH)
        url = cls._safe_url(paper.get("url"))
        external = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
        doi = truncate(external.get("DOI"), MAX_URL_LENGTH)
        if not url and doi:
            url = truncate(f"https://doi.org/{doi}", MAX_URL_LENGTH)
        authors = [
            truncate(item.get("name"), MAX_SEARCH_CONTENT_LENGTH)
            for item in (paper.get("authors") if isinstance(paper.get("authors"), list) else [])
            if isinstance(item, dict) and truncate(item.get("name"), MAX_SEARCH_CONTENT_LENGTH)
        ]
        published = truncate(paper.get("publicationDate") or paper.get("year"), 64)
        journal_data = paper.get("journal")
        journal = truncate(
            journal_data.get("name") if isinstance(journal_data, dict) else paper.get("venue"),
            MAX_SEARCH_CONTENT_LENGTH,
        )
        abstract = truncate(paper.get("abstract"), MAX_SEARCH_CONTENT_LENGTH)
        content = abstract or " ".join(str(value) for value in (title, ", ".join(authors), journal, published) if value)
        if not all((source_id, title, url, content)):
            return None
        row: dict[str, Any] = {
            "title": title, "url": url, "content": truncate(content, MAX_SEARCH_CONTENT_LENGTH),
            "source": "semantic_scholar", "source_id": source_id,
            "full_text_candidates": cls._candidates(paper),
        }
        optional = {
            "published": published, "authors": authors, "doi": doi, "journal": journal,
            "arxiv_id": truncate(external.get("ArXiv"), MAX_URL_LENGTH),
            "pmid": truncate(external.get("PubMed"), MAX_URL_LENGTH),
            "corpus_id": truncate(external.get("CorpusId"), MAX_URL_LENGTH),
        }
        row.update({key: value for key, value in optional.items() if value})
        citations = paper.get("citationCount")
        if type(citations) is int and citations >= 0:
            row["citation_count"] = citations
        row.update(empty_full_text_fields())
        return row

    @classmethod
    def _candidates(cls, paper: dict[str, Any]) -> list[dict[str, Any]]:
        pdf = paper.get("openAccessPdf")
        url = cls._safe_url(pdf.get("url")) if isinstance(pdf, dict) else ""
        return ([{"url": url, "format": "pdf", "source": "semantic_scholar",
                  "kind": "semantic_scholar_pdf", "priority": 60}] if url else [])

    @staticmethod
    def _safe_url(value: Any) -> str:
        url = str(value or "").strip()
        try:
            parsed = urlsplit(url)
        except ValueError:
            return ""
        return truncate(url, MAX_URL_LENGTH) if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc else ""
