"""Full bundled lexicon at production batch size; no network or credentials."""

import asyncio
from contextlib import suppress
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

from openjiuwen_deepsearch.algorithm.report.evidence import EvidenceMixin
from openjiuwen_deepsearch.algorithm.report.report_common import MAX_EXTRACT_DOC_CHARS
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.utils.moderation import mask
from openjiuwen_deepsearch.utils.moderation.content import MaskableText
from openjiuwen_deepsearch.utils.moderation.retry import moderation_retry_scope


class _Model:
    def __init__(self):
        self.calls = []

    async def stream(self, **kwargs):
        self.calls.append(deepcopy(kwargs["messages"]))
        await asyncio.sleep(0)
        if len(self.calls) == 1:
            raise ValueError("Input data may contain inappropriate content")
        yield AssistantMessageChunk(content=json.dumps({"documents": [
            {"doc_index": 0, "passages": [{"text": "evidence", "rationale_ids": ["r1"]}]},
        ]}))


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(llm_utils, "_is_llm_stats_enabled", lambda: False)
    monkeypatch.setattr(llm_utils, "_resolve_agent_llm_timeout", lambda **kwargs: None)
    return _Model()


@pytest.mark.asyncio
@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm"])
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
@pytest.mark.parametrize("unbroken", [False, True], ids=["prose", "long-token"])
async def test_full_batch_heartbeat(model, warm, enabled, unbroken, record_property):
    mask._load_lexicon_bundled.cache_clear()
    mask._compiled_matchers.cache_clear()
    if warm:
        mask.mask_text("warm up", mask.get_lexicon())
    prose = "这是一份用于研究资料分析的正常文本，包含项目数据与说明。\n" * 600
    body = ("研" * MAX_EXTRACT_DOC_CHARS if unbroken else prose)[:MAX_EXTRACT_DOC_CHARS - 5] + " sex "
    docs = [{"title": f"doc-{index}", "url": f"https://normal.example/{index}",
             "original_content": body} for index in range(5)]
    original = deepcopy(docs)
    reporter = SimpleNamespace(_llm={"model": model, "model_name": "test", "moderation_mask_retry": enabled})
    loop = asyncio.get_running_loop()
    beats = [loop.time()]

    async def heartbeat():
        while True:
            await asyncio.sleep(0.01)
            beats.append(loop.time())

    ticker = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = loop.time()
    try:
        result, returned_docs, error = await EvidenceMixin._extract_batch(
            reporter, docs, 0, "r1: evidence", {"section_task": "research task", "max_retries": 2},
        )
    finally:
        elapsed = loop.time() - start
        beats.append(loop.time())
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker
    gap = max(right - left for left, right in zip(beats, beats[1:]))
    record_property("elapsed_seconds", elapsed)
    record_property("max_heartbeat_gap_seconds", gap)
    assert gap < 0.25, f"event loop stalled for {gap:.3f}s"
    assert elapsed < 2.5, f"batch took {elapsed:.3f}s"
    assert len(model.calls) == 2
    assert result["documents"] and error == ""
    assert returned_docs == docs == original
    first_user = next(message.content for message in model.calls[0] if message.role == "user")
    next_user = next(message.content for message in model.calls[1] if message.role == "user")
    assert " sex " in first_user
    assert (" *** " in next_user) is enabled
    assert (first_user != next_user) is enabled


@pytest.mark.asyncio
async def test_cancellation_during_input_preparation_does_not_call_model_again(model):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    body = "research sex"

    def slow_masker(terms):
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("test worker was not released")
            return mask.mask_text(body, terms)
        finally:
            finished.set()

    messages = [{"role": "user", "content": MaskableText(body, slow_masker)}]
    config = {"model": model, "model_name": "test", "moderation_mask_retry": True}

    @moderation_retry_scope
    async def operation():
        for attempt in range(2):
            try:
                return await llm_utils.ainvoke_llm_with_stats(llm=config, messages=messages, agent_name="AI")
            except Exception:
                if attempt == 1:
                    raise

    task = asyncio.create_task(operation())
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.25)
        assert len(model.calls) == 1
        assert messages[0]["content"] == body
    finally:
        release.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(finished.wait, 5)
    assert finished.is_set()
    assert len(model.calls) == 1
