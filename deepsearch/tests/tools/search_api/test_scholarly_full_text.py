import logging
import threading

import httpx
import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.full_text import (
    FullTextConfig,
    SCHOLARLY_DEFER_FULL_TEXT,
    defer_scholarly_full_text,
    rank_full_text_candidates,
    resolve_scholarly_full_text,
    should_fetch_full_text,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search import full_text


class Wrapper:
    fetch_full_text = True


def test_defer_context_is_scoped_and_restored():
    assert should_fetch_full_text(Wrapper()) is True
    with defer_scholarly_full_text():
        assert SCHOLARLY_DEFER_FULL_TEXT.get() is True
        assert should_fetch_full_text(Wrapper()) is False
    assert should_fetch_full_text(Wrapper()) is True


def test_candidate_priority_and_url_deduplication():
    candidates = [
        {"url": "https://x/paper.pdf", "kind": "semantic_scholar_pdf"},
        {"url": "https://x/pmc", "kind": "pmc_jats"},
        {"url": "https://x/paper.pdf/", "kind": "semantic_scholar_pdf"},
        {"url": "javascript:bad", "kind": "pmc_jats"},
    ]

    ranked = rank_full_text_candidates(candidates)

    assert [item["kind"] for item in ranked] == ["pmc_jats", "semantic_scholar_pdf"]


@pytest.mark.asyncio
async def test_resolver_stops_after_first_usable_candidate_and_preserves_abstract():
    calls = []

    async def fetch(url, config):
        calls.append(url)
        return b"<article>" + (b"full article text " * 20) + b"</article>"

    row = {
        "content": "abstract",
        "full_text_candidates": [
            {"url": "https://x/article", "kind": "repository_html", "format": "html"},
            {"url": "https://x/backup", "kind": "semantic_scholar_pdf", "format": "pdf"},
        ],
    }
    await resolve_scholarly_full_text(
        row, FullTextConfig(minimum_text_length=10), fetcher=fetch
    )

    assert calls == ["https://x/article"]
    assert row["content"] == "abstract"
    assert row["full_text_status"] == "available"


@pytest.mark.asyncio
async def test_resolver_logs_safe_failure_type_before_falling_back(caplog):
    async def fetch(url, config):
        if url == "https://x/primary":
            raise RuntimeError("sensitive upstream details")
        return b"<article>usable fallback full text</article>"

    row = {
        "content": "abstract",
        "full_text_candidates": [
            {"url": "https://x/primary", "kind": "pmc_jats", "format": "html"},
            {"url": "https://x/backup", "kind": "repository_html", "format": "html"},
        ],
    }

    with caplog.at_level(logging.DEBUG):
        await resolve_scholarly_full_text(
            row,
            FullTextConfig(minimum_text_length=1),
            fetcher=fetch,
        )

    assert row["full_text_status"] == "available"
    assert "RuntimeError" in caplog.text
    assert "sensitive upstream details" not in caplog.text
    assert "https://x/primary" not in caplog.text


@pytest.mark.asyncio
async def test_download_rejects_unsafe_url_before_opening_client(monkeypatch):
    def reject(_url):
        raise ValueError("unsafe")

    monkeypatch.setattr(full_text, "validate_runtime_request_url", reject, raising=False)
    monkeypatch.setattr(
        full_text.httpx,
        "AsyncClient",
        lambda **_kwargs: pytest.fail("unsafe URL reached the HTTP client"),
    )

    with pytest.raises(ValueError, match="unsafe"):
        await full_text._download("http://127.0.0.1/private", FullTextConfig())


@pytest.mark.asyncio
async def test_resolver_skips_fetch_when_candidate_source_is_disabled():
    calls = []

    async def fetch(url, config):
        calls.append((url, config))
        return b"should not be fetched"

    row = {
        "content": "abstract",
        "full_text_candidates": [
            {"url": "https://example.org/a", "source": "semantic_scholar", "format": "html"},
        ],
    }
    await resolve_scholarly_full_text(
        row,
        configs_by_source={"semantic_scholar": FullTextConfig(enabled=False)},
        fetcher=fetch,
    )

    assert calls == []
    assert row["full_text_status"] == "unavailable"


@pytest.mark.asyncio
async def test_resolver_runs_extraction_off_event_loop_thread(monkeypatch):
    event_loop_thread = threading.get_ident()
    extraction_threads = []

    def extract(data, candidate, limit, max_pdf_pages):
        extraction_threads.append(threading.get_ident())
        return "usable full text", False

    async def fetch(url, config):
        return b"payload"

    monkeypatch.setattr(full_text, "_extract_text", extract)
    row = {
        "content": "abstract",
        "full_text_candidates": [
            {"url": "https://example.org/a", "source": "semantic_scholar", "format": "html"},
        ],
    }
    await resolve_scholarly_full_text(
        row,
        FullTextConfig(minimum_text_length=1),
        fetcher=fetch,
    )

    assert extraction_threads
    assert extraction_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_download_validates_redirect_target_before_following(monkeypatch):
    validated = []
    requested = []

    def validate(url):
        validated.append(url)
        if "127.0.0.1" in url:
            raise ValueError("unsafe redirect")

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(full_text, "validate_runtime_request_url", validate)
    monkeypatch.setattr(
        full_text.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(ValueError, match="unsafe redirect"):
        await full_text._download("https://public.example/paper", FullTextConfig())

    assert requested == ["https://public.example/paper"]
    assert validated[-1] == "http://127.0.0.1/private"


@pytest.mark.asyncio
async def test_download_stops_streaming_at_byte_limit(monkeypatch):
    consumed = []

    class ChunkStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b"1234", b"5678", b"never-read"):
                consumed.append(chunk)
                yield chunk

    def handler(_request):
        return httpx.Response(200, stream=ChunkStream())

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(full_text, "validate_runtime_request_url", lambda _url: None)
    monkeypatch.setattr(
        full_text.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(ValueError, match="size exceeds"):
        await full_text._download(
            "https://public.example/paper", FullTextConfig(max_download_bytes=6)
        )

    assert consumed == [b"1234", b"5678"]
