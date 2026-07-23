"""Regression tests for figure placeholder H1 aggregation error handling.

A single failing H2 subsection must not crash the whole H1 section:
`_process_section_h2` returns `[]` on error, and `_process_section_h1`
skips any non-list result (None or an Exception surfaced by
`asyncio.gather(..., return_exceptions=True)`).
"""

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.chart_generation.figure_placeholders import (
    FigurePlaceholderGenerator,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_process_section_h2_returns_empty_list_on_llm_error():
    """A failed LLM call yields an empty list, never None."""
    generator = FigurePlaceholderGenerator("dummy-model")
    section = {"title": "Section", "content": "some paragraph\nanother paragraph"}

    with patch(
        "openjiuwen_deepsearch.algorithm.chart_generation.figure_placeholders.call_model",
        new=AsyncMock(side_effect=RuntimeError("llm down")),
    ):
        result = await generator._process_section_h2(section)

    assert result == []


@pytest.mark.asyncio
async def test_process_section_h1_skips_none_and_exception_results():
    """H1 aggregation tolerates failing subsections without raising TypeError."""
    generator = FigurePlaceholderGenerator("dummy-model")
    sections = [
        {"title": "good", "content": "c1"},
        {"title": "none-fail", "content": "c2"},
        {"title": "exc-fail", "content": "c3"},
    ]
    good_task = [{"chart_desc": "x"}]

    # None simulates the pre-fix silent-failure return; the Exception simulates
    # an uncaught error surfaced by gather(return_exceptions=True).
    with patch.object(
        generator,
        "_process_section_h2",
        new=AsyncMock(side_effect=[good_task, None, RuntimeError("boom")]),
    ):
        result = await generator._process_section_h1(sections)

    assert result == [{"chart_desc": "x", "chart_id_in_section": 1}]
