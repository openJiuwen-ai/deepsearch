"""任务级正文缓存的并发、预算和增强入口集成测试。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.webpage_cache import TaskWebpageFetchCache
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import (
    WebPageEnrichmentNode, WebPageEvidenceContent,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    session_context, webpage_fetch_cache_context,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


@pytest.mark.asyncio
async def test_reuse_copies_exact_urls_and_tracks_body_size():
    cache = TaskWebpageFetchCache()
    fetch = AsyncMock(return_value={"content": "x" * 300, "metadata": {"tags": ["original"]}})
    try:
        result = await cache.get_or_fetch("https://a/?x=1&y=2", deadline(), 200, fetch)
        result["content"] = "changed"
        result["primary_content"] = "changed view"
        result["metadata"]["tags"].append("changed")
        reused = await cache.get_or_fetch("https://a/?x=1&y=2", deadline(), 200, fetch)
        assert reused["content"] == "x" * 300
        assert reused["primary_content"] == "x" * 300
        assert reused["metadata"]["tags"] == ["original"]
        await cache.get_or_fetch("https://a/?y=2&x=1", deadline(), 200, fetch)
        assert fetch.await_count == 2
        assert cache.snapshot()["cache_entries"] == 2
        assert cache.snapshot()["cache_peak_bytes"] > 0
    finally:
        await cache.close()
    assert cache.snapshot()["cache_bytes"] == 0


@pytest.mark.asyncio
async def test_concurrent_same_url_coalesces_and_different_urls_overlap():
    cache = TaskWebpageFetchCache()
    started = asyncio.Queue()
    release = asyncio.Event()

    async def fetch(end, required):
        await started.put(required)
        await release.wait()
        return {"content": "x" * 300}

    tasks = [asyncio.create_task(cache.get_or_fetch(url, deadline(), 200, fetch)) for url in ("a", "a", "b")]
    try:
        await asyncio.wait_for(started.get(), 1)
        await asyncio.wait_for(started.get(), 1)
        assert cache.snapshot()["fetch_started"] == 2
        assert cache.snapshot()["cache_wait"] == 1
        release.set()
        assert all(result["content"] == "x" * 300 for result in await asyncio.gather(*tasks))
    finally:
        release.set()
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [{}, {"content": "short"}, {"content": "%PDF-" + "x" * 300}, ValueError("failure")])
async def test_failure_is_not_cached_and_next_request_retries(failed):
    cache = TaskWebpageFetchCache()
    fetch = AsyncMock(side_effect=[failed, {"content": "x" * 300}])
    try:
        if isinstance(failed, Exception):
            with pytest.raises(type(failed)):
                await cache.get_or_fetch("a", deadline(), 200, fetch)
        else:
            assert await cache.get_or_fetch("a", deadline(), 200, fetch) == {}
        assert cache.snapshot()["cache_entries"] == 0
        assert (await cache.get_or_fetch("a", deadline(), 200, fetch))["content"] == "x" * 300
        assert fetch.await_count == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_insufficient_cached_body_refetches_and_failure_preserves_old():
    cache = TaskWebpageFetchCache()
    fetch = AsyncMock(side_effect=[{"content": "x" * 300}, {}, {"content": "y" * 600}])
    try:
        await cache.get_or_fetch("a", deadline(), 200, fetch)
        assert await cache.get_or_fetch("a", deadline(), 500, fetch) == {}
        assert (await cache.get_or_fetch("a", deadline(), 200, fetch))["content"] == "x" * 300
        assert (await cache.get_or_fetch("a", deadline(), 500, fetch))["content"] == "y" * 600
        assert (await cache.get_or_fetch("a", deadline(), 200, fetch))["content"] == "y" * 600
        assert fetch.await_count == 3
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_higher_threshold_waiter_refetches_with_its_original_deadline():
    cache = TaskWebpageFetchCache()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fetch(end, required):
        calls.append((end, required))
        if len(calls) == 1:
            started.set()
            await release.wait()
        return {"content": "x" * required}

    low = asyncio.create_task(cache.get_or_fetch("a", deadline(), 200, fetch))
    await started.wait()
    high_deadline = deadline()
    high = asyncio.create_task(cache.get_or_fetch("a", high_deadline, 500, fetch))
    await asyncio.sleep(0)
    release.set()
    try:
        low_result, high_result = await asyncio.gather(low, high)
        assert len(low_result["content"]) == 200
        assert len(high_result["content"]) == 500
        assert calls[1] == (high_deadline, 500)
    finally:
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["waiter_timeout", "waiter_cancel", "initiator_cancel"])
async def test_one_caller_timeout_or_cancellation_does_not_cancel_shared_fetch(outcome):
    cache = TaskWebpageFetchCache()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(end, required):
        started.set()
        await release.wait()
        return {"content": "x" * 300}

    producer = asyncio.create_task(cache.get_or_fetch("a", deadline(), 200, fetch))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_fetch("a", deadline(0.03 if outcome == "waiter_timeout" else 1), 200, fetch))
    await asyncio.sleep(0)
    try:
        departing = producer if outcome == "initiator_cancel" else waiter
        remaining = waiter if outcome == "initiator_cancel" else producer
        if outcome != "waiter_timeout":
            departing.cancel()
        with pytest.raises(TimeoutError if outcome == "waiter_timeout" else asyncio.CancelledError):
            await departing
        assert not remaining.done()
        release.set()
        assert (await remaining)["content"] == "x" * 300
        assert cache.snapshot()["fetch_started"] == 1
    finally:
        release.set()
        await cache.close()


@pytest.mark.asyncio
async def test_close_blocks_late_result_even_if_callback_suppresses_cancellation():
    cache = TaskWebpageFetchCache()
    started = asyncio.Event()

    async def fetch(end, required):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return {"content": "x" * 300}

    request = asyncio.create_task(cache.get_or_fetch("a", deadline(), 200, fetch))
    await started.wait()
    await cache.close()
    assert await request == {}
    assert cache.snapshot()["cache_entries"] == 0
    assert cache.snapshot()["cache_bytes"] == 0
    assert await cache.get_or_fetch("a", deadline(), 200, fetch) == {}
    await cache.close()


@pytest.mark.asyncio
async def test_shared_failure_is_not_immediately_retried_by_waiters():
    cache = TaskWebpageFetchCache()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(end, required):
        started.set()
        await release.wait()
        return {}

    first = asyncio.create_task(cache.get_or_fetch("a", deadline(), 200, fetch))
    await started.wait()
    second = asyncio.create_task(cache.get_or_fetch("a", deadline(), 500, fetch))
    await asyncio.sleep(0)
    release.set()
    try:
        assert await asyncio.gather(first, second) == [{}, {}]
        assert cache.snapshot()["fetch_started"] == 1
        assert cache.snapshot()["fetch_failed"] == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_sensitive_events_and_summary_do_not_contain_url_or_body(monkeypatch, caplog):
    monkeypatch.setattr(LogManager, "is_sensitive", lambda: True)
    caplog.set_level("INFO")
    cache = TaskWebpageFetchCache()
    fetch = AsyncMock(return_value={"content": "private-body" * 30})
    try:
        await cache.get_or_fetch("https://secret.example/private", deadline(), 200, fetch)
        await cache.get_or_fetch("https://secret.example/private", deadline(), 200, fetch)
    finally:
        await cache.close()
    assert "enrichment_cache_summary" in caplog.text
    assert "url_redacted" in caplog.text
    assert "secret.example" not in caplog.text
    assert "private-body" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("compression_failure", [True, False])
async def test_compression_failure_or_success_keeps_raw_cache(monkeypatch, compression_failure):
    cache = TaskWebpageFetchCache()
    token = webpage_fetch_cache_context.set(cache)
    session_token = session_context.set(None)
    node = WebPageEnrichmentNode()
    fetch = AsyncMock(return_value={"content": "new webpage " * 30})
    monkeypatch.setattr(node, "_fetch_webpage_before_deadline", fetch)
    monkeypatch.setattr(node, "_compress_content", AsyncMock(return_value=(
        None if compression_failure else WebPageEvidenceContent(original_content="facts without numbers")
    )))
    doc = {"url": "https://a", "original_content": "42 videos", "key_passages": ["42 videos"]}
    state = {"candidates": [{"url": "https://a", "doc_index": 0}]}
    try:
        results = [await node._enrich_candidate(state, [doc], 0) for _ in range(2)]
        if compression_failure:
            assert results == [None, None]
        else:
            assert all(result["doc"]["original_content"] == "facts without numbers" for result in results)
            assert all(result["doc"]["enrichment"]["webpage_fetched"] for result in results)
        assert fetch.await_count == 1
        assert node._compress_content.await_count == 2
        assert doc["original_content"] == "42 videos"
    finally:
        session_context.reset(session_token)
        webpage_fetch_cache_context.reset(token)
        await cache.close()
