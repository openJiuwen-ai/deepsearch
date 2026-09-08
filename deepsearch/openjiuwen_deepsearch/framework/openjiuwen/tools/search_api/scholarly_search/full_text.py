"""Shared controls for scholarly full-text retrieval."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from io import BytesIO
from functools import partial
from typing import Any, Awaitable, Callable, Iterator, Sequence
from urllib.parse import urljoin

import httpx
import pypdfium2 as pdfium
from bs4 import BeautifulSoup

from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import ssl_verify
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_scholarly_full_text_url


# Kept as a local seam so download security can be tested without DNS/network access.
validate_runtime_request_url = validate_scholarly_full_text_url
_PARSER_SEMAPHORE = asyncio.Semaphore(2)
_PARSER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scholarly-full-text")
logger = logging.getLogger(__name__)


SCHOLARLY_DEFER_FULL_TEXT: ContextVar[bool] = ContextVar(
    "scholarly_defer_full_text", default=False
)


@contextmanager
def defer_scholarly_full_text(enabled: bool = True) -> Iterator[None]:
    token = SCHOLARLY_DEFER_FULL_TEXT.set(enabled)
    try:
        yield
    finally:
        SCHOLARLY_DEFER_FULL_TEXT.reset(token)


def should_fetch_full_text(wrapper: Any) -> bool:
    return bool(getattr(wrapper, "fetch_full_text", False)) and not SCHOLARLY_DEFER_FULL_TEXT.get()


_CANDIDATE_PRIORITY = {
    "pmc_jats": 10,
    "arxiv_html": 20,
    "repository_html": 30,
    "arxiv_pdf": 40,
    "semantic_scholar_pdf": 60,
}


def rank_full_text_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            continue
        canonical = url.rstrip("/")
        unique.setdefault(canonical, {**candidate, "url": canonical})
    return sorted(
        unique.values(),
        key=lambda item: (_CANDIDATE_PRIORITY.get(str(item.get("kind")), 100), item["url"]),
    )


@dataclass(frozen=True)
class FullTextConfig:
    enabled: bool = True
    timeout_seconds: float = 30.0
    max_text_length: int = MAX_COLLECTOR_DOC_CONTENT_LENGTH
    max_download_bytes: int = 25 * 1024 * 1024
    minimum_text_length: int = 200
    max_pdf_pages: int = 200
    parse_timeout_seconds: float = 30.0
    max_redirects: int = 5


async def _download(url: str, config: FullTextConfig) -> bytes:
    current_url = url
    validate_runtime_request_url(current_url)
    async with httpx.AsyncClient(
        verify=ssl_verify(), timeout=config.timeout_seconds, follow_redirects=False
    ) as client:
        for redirect_count in range(config.max_redirects + 1):
            validate_runtime_request_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect_count >= config.max_redirects:
                        raise ValueError("full-text redirect limit exceeded")
                    current_url = urljoin(current_url, location)
                    validate_runtime_request_url(current_url)
                    continue
                response.raise_for_status()
                raw_length = response.headers.get("content-length")
                if raw_length:
                    try:
                        declared = int(raw_length)
                    except (TypeError, ValueError):
                        declared = 0
                    if declared > config.max_download_bytes:
                        raise ValueError("full-text download size exceeds limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > config.max_download_bytes:
                        raise ValueError("full-text download size exceeds limit")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("full-text redirect limit exceeded")


def _extract_pdf(data: bytes, limit: int, max_pages: int) -> tuple[str, bool]:
    document = pdfium.PdfDocument(BytesIO(data))
    try:
        parts: list[str] = []
        page_count = len(document)
        for page_index, page in enumerate(document):
            if page_index >= max(1, max_pages):
                return "\n\n".join(parts)[:limit], True
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
                return text[:limit], len(text) > limit or page_index + 1 < page_count
        return "\n\n".join(parts), False
    finally:
        document.close()


def _extract_text(
        data: bytes,
        candidate: dict[str, Any],
        limit: int,
        max_pdf_pages: int,
) -> tuple[str, bool]:
    if str(candidate.get("format") or "").casefold() == "pdf" or str(candidate.get("kind") or "").endswith("_pdf"):
        return _extract_pdf(data, limit, max_pdf_pages)
    soup = BeautifulSoup(data, "html.parser")
    for element in soup.select("script, style, nav, header, footer, aside"):
        element.decompose()
    text = "\n\n".join(soup.stripped_strings)
    return text[:limit], len(text) > limit


async def resolve_scholarly_full_text(
    row: dict[str, Any],
    config: FullTextConfig = FullTextConfig(),
    fetcher: Callable[[str, FullTextConfig], Awaitable[bytes]] = _download,
    configs_by_source: dict[str, FullTextConfig] | None = None,
) -> dict[str, Any]:
    candidates = rank_full_text_candidates(row.get("full_text_candidates") or [])
    if not candidates:
        row.setdefault("full_text_status", "unavailable")
        return row
    attempted = False
    for candidate in candidates:
        candidate_source = str(candidate.get("source") or "").casefold()
        candidate_config = (configs_by_source or {}).get(candidate_source, config)
        if not candidate_config.enabled:
            continue
        attempted = True
        try:
            data = await fetcher(candidate["url"], candidate_config)
            async with _PARSER_SEMAPHORE:
                loop = asyncio.get_running_loop()
                text, truncated = await asyncio.wait_for(
                    loop.run_in_executor(
                        _PARSER_EXECUTOR,
                        partial(
                            _extract_text,
                            data,
                            candidate,
                            candidate_config.max_text_length,
                            candidate_config.max_pdf_pages,
                        ),
                    ),
                    timeout=candidate_config.parse_timeout_seconds,
                )
            if len(text) < candidate_config.minimum_text_length:
                continue
            row.update({
                "full_text": text,
                "original_content": text,
                "content_type": "full_text",
                "full_text_status": "available",
                "full_text_url": candidate["url"],
                "full_text_format": candidate.get("format") or "html",
                "full_text_truncated": truncated,
            })
            return row
        except Exception as exc:
            logger.debug(
                "Unable to resolve scholarly full-text candidate; error_type=%s",
                type(exc).__name__,
            )
            continue
    row["full_text_status"] = "failed" if attempted else "unavailable"
    return row
