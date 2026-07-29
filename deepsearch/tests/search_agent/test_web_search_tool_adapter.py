from __future__ import annotations

import pytest

from openjiuwen_deepsearch.algorithm.search_tools import web_search_tool as web_search_tool_module
from openjiuwen_deepsearch.algorithm.search_tools.web_search_tool import WebSearch

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "rows", "expected_fragments"),
    [
        (
            "tavily",
            [
                {
                    "title": "Tavily result",
                    "url": "https://example.com/tavily",
                    "content": "Tavily summary",
                    "source": "tavily",
                }
            ],
            [
                'Results for query "energy policy" (1 entries):',
                "[Tavily result](https://example.com/tavily)",
                "Origin: tavily",
                "Tavily summary",
            ],
        ),
        (
            "jina",
            [
                {
                    "title": "Jina result",
                    "url": "https://example.com/jina",
                    "content": "Jina summary",
                    "source": "jina",
                }
            ],
            [
                "[Jina result](https://example.com/jina)",
                "Origin: jina",
                "Jina summary",
            ],
        ),
        (
            "serper",
            [
                {
                    "title": "Serper result",
                    "link": "https://example.com/serper",
                    "snippet": "Serper snippet",
                    "source": "serper",
                    "date": "2026-07-09",
                }
            ],
            [
                "[Serper result](https://example.com/serper)",
                "Published: 2026-07-09",
                "Origin: serper",
                "Serper snippet",
            ],
        ),
    ],
)
async def test_web_search_adapter_formats_registered_engine_results(
    monkeypatch,
    tmp_path,
    engine_name: str,
    rows: list[dict[str, str]],
    expected_fragments: list[str],
) -> None:
    async def _fake_run_web_search(query: str, active_engine_name: str) -> dict:
        assert query == "energy policy"
        assert active_engine_name == engine_name
        return {"search_engine": active_engine_name, "search_results": rows}

    monkeypatch.setattr(
        web_search_tool_module,
        "get_web_search_api_wrapper",
        lambda: (engine_name, object()),
    )
    monkeypatch.setattr(
        web_search_tool_module,
        "run_web_search",
        _fake_run_web_search,
    )

    tool = WebSearch({"web_search_log_file": str(tmp_path / "web_search.jsonl")})
    result = await tool.acall({"query": "energy policy", "log_search": False})

    for fragment in expected_fragments:
        assert fragment in result


@pytest.mark.asyncio
async def test_web_search_adapter_returns_controlled_error_text(monkeypatch, tmp_path) -> None:
    async def _fake_run_web_search(query: str, active_engine_name: str) -> dict:
        assert query == "failing query"
        assert active_engine_name == "jina"
        return {
            "search_engine": active_engine_name,
            "search_results": [],
            "error": "Error when run web search jina: boom",
        }

    monkeypatch.setattr(
        web_search_tool_module,
        "get_web_search_api_wrapper",
        lambda: ("jina", object()),
    )
    monkeypatch.setattr(
        web_search_tool_module,
        "run_web_search",
        _fake_run_web_search,
    )

    tool = WebSearch({"web_search_log_file": str(tmp_path / "web_search.jsonl")})
    result = await tool.acall({"query": "failing query", "log_search": False})

    assert result == (
        'No usable results for query "failing query". '
        "Error: Error when run web search jina: boom"
    )


@pytest.mark.asyncio
async def test_web_search_adapter_reports_missing_registered_engine(tmp_path) -> None:
    tool = WebSearch({"web_search_log_file": str(tmp_path / "web_search.jsonl")})

    result = await tool.acall({"query": "missing engine", "log_search": False})

    assert result == (
        'No usable results for query "missing engine". '
        "Error: Active web search engine is not initialized."
    )
