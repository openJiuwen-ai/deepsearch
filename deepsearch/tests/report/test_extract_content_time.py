# -*- coding: UTF-8 -*-
"""Tests for content_time extraction in _extract_and_score_documents.

Verifies that when the temporal constraint_type is ``content_date`` the
``content_time`` field returned by the passages extractor LLM is attached to
each merged passage_dict, and that for ``source_date`` (or no scope) it is
``None`` even if the LLM happened to return one.
"""
import datetime

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
)


def _content_date_intent() -> dict:
    return ResearchIntent(
        temporal_scope=TemporalScope(
            constraint_type="content_date",
            start_date=datetime.date(2018, 1, 1),
            end_date=datetime.date(2023, 12, 31),
        )
    ).model_dump()


def _source_date_intent() -> dict:
    return ResearchIntent(
        temporal_scope=TemporalScope(
            constraint_type="source_date",
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 12, 31),
        )
    ).model_dump()


def _raw_passages() -> list:
    return [
        {
            "url": "https://example.com/1",
            "title": "Doc A",
            "doc_time": "2024-01-01",
            "publish_time": "2024-01-15",
            "source": "web",
            "original_content": "In 2019, X happened.",
        }
    ]


def _rationales() -> list:
    return [{"id": "r1", "description": "core facts", "type": "primary"}]


def _llm_doc_result() -> dict:
    """A single document result as emitted by the passages extractor LLM,
    including a content_time field (present regardless of scenario; the merge
    logic decides whether to keep it)."""
    return {
        "documents": [
            {
                "doc_index": 0,
                "passages": [
                    {
                        "text": "In 2019, X happened.",
                        "reliability": 0.8,
                        "data_density": 0.5,
                        "content_time": {
                            "start": "2019-01-01",
                            "end": "2019-12-31",
                        },
                        "scores": {"r1": {"coverage": 0.9}},
                    }
                ],
            }
        ]
    }


def _make_reporter_with_mocked_extract(monkeypatch, captured_ctx: list) -> Reporter:
    """Build a Reporter bypassing __init__ and replace _extract_batch with a
    deterministic async stub that records the section_ctx it received."""
    reporter = Reporter.__new__(Reporter)

    async def _fake_extract_batch(batch, batch_idx, rationales_text, section_ctx):
        captured_ctx.append(section_ctx)
        return _llm_doc_result(), batch, ""

    monkeypatch.setattr(reporter, "_extract_batch", _fake_extract_batch)
    return reporter


@pytest.mark.asyncio
async def test_content_time_attached_for_content_date(monkeypatch):
    captured_ctx: list = []
    reporter = _make_reporter_with_mocked_extract(monkeypatch, captured_ctx)

    current_inputs = {
        "section_idx": 1,
        "section_task": "1 Background",
        "section_description": "background",
        "research_intent": _content_date_intent(),
        "max_generate_retry_num": 1,
    }

    result, error = await reporter._extract_and_score_documents(
        current_inputs, _raw_passages(), _rationales()
    )

    assert error == ""
    passages = result["filtered_passages"]
    assert len(passages) == 1
    # content_date scenario keeps the LLM-emitted content_time on the passage.
    assert passages[0]["content_time"] == {
        "start": "2019-01-01",
        "end": "2019-12-31",
    }
    # The flag must have been propagated into section_ctx for _extract_batch.
    assert captured_ctx and captured_ctx[0].get("extract_content_time") is True


@pytest.mark.asyncio
async def test_content_time_none_for_source_date(monkeypatch):
    captured_ctx: list = []
    reporter = _make_reporter_with_mocked_extract(monkeypatch, captured_ctx)

    current_inputs = {
        "section_idx": 1,
        "section_task": "1 Background",
        "section_description": "background",
        "research_intent": _source_date_intent(),
        "max_generate_retry_num": 1,
    }

    result, error = await reporter._extract_and_score_documents(
        current_inputs, _raw_passages(), _rationales()
    )

    assert error == ""
    passages = result["filtered_passages"]
    assert len(passages) == 1
    # source_date scenario: extract_content_time is False, so content_time is
    # dropped to None even though the LLM returned a value.
    assert passages[0]["content_time"] is None
    assert captured_ctx and captured_ctx[0].get("extract_content_time") is False


@pytest.mark.asyncio
async def test_content_time_none_without_temporal_scope(monkeypatch):
    captured_ctx: list = []
    reporter = _make_reporter_with_mocked_extract(monkeypatch, captured_ctx)

    current_inputs = {
        "section_idx": 1,
        "section_task": "1 Background",
        "section_description": "background",
        "research_intent": {},  # no temporal_scope
        "max_generate_retry_num": 1,
    }

    result, error = await reporter._extract_and_score_documents(
        current_inputs, _raw_passages(), _rationales()
    )

    assert error == ""
    passages = result["filtered_passages"]
    assert len(passages) == 1
    assert passages[0]["content_time"] is None
    assert captured_ctx and captured_ctx[0].get("extract_content_time") is False
