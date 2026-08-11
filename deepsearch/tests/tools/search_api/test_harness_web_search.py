# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from contextlib import contextmanager
from unittest.mock import patch

import pytest


def test_harness_web_search_wrapper_fetches_urls_from_web_tools():
    """Paid wrapper should call web_tools search and fetch page content for URL-only results."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
        BochaSearchAPIWrapper,
    )

    wrapper = BochaSearchAPIWrapper(
        search_api_key=bytearray(b"bocha-key"),
        search_url="",
        max_web_search_results=2,
    )

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "WebPaidSearchTool._bocha_search_sync",
        return_value={
            "provider": "bocha",
            "answer": "fallback answer",
            "urls": ["https://example.com/news"],
        },
    ) as mock_search, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "WebFetchWebpageAdapter.fetch_webpage_sync",
        return_value={
            "url": "https://example.com/news",
            "status_code": 200,
            "title": "Fetched title",
            "content": "Fetched body",
        },
    ) as mock_fetch:
        result = wrapper.results("test query")

    assert result == [
        {
            "title": "Fetched title",
            "url": "https://example.com/news",
            "content": "Fetched body",
            "source": "bocha",
        }
    ]
    mock_search.assert_called_once_with(query="test query", max_results=2, timeout_seconds=60)
    mock_fetch.assert_called_once_with("https://example.com/news", 60)


def test_harness_web_search_wrapper_truncates_prefetched_content():
    """Paid wrapper should keep long fetched content bounded before returning results."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
        BochaSearchAPIWrapper,
    )
    from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH

    wrapper = BochaSearchAPIWrapper(
        search_api_key=bytearray(b"bocha-key"),
        search_url="",
        max_web_search_results=1,
    )

    long_content = "A" * (MAX_COLLECTOR_DOC_CONTENT_LENGTH + 200)
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "WebPaidSearchTool._bocha_search_sync",
        return_value={
            "provider": "bocha",
            "answer": "fallback answer",
            "urls": ["https://example.com/long"],
        },
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "WebFetchWebpageAdapter.fetch_webpage_sync",
        return_value={
            "url": "https://example.com/long",
            "status_code": 200,
            "title": "Long title",
            "content": long_content,
        },
    ):
        result = wrapper.results("test query")

    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/long"
    assert len(result[0]["content"]) == MAX_COLLECTOR_DOC_CONTENT_LENGTH
    assert result[0]["content"].endswith("...")


@pytest.mark.asyncio
async def test_harness_web_search_wrapper_async_delegates_to_sync_results():
    """Paid wrapper should expose the same async interface as existing search wrappers."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
        PerplexitySearchAPIWrapper,
    )

    wrapper = PerplexitySearchAPIWrapper(
        search_api_key=bytearray(b"pplx-key"),
        search_url="",
        max_web_search_results=1,
    )

    with patch.object(
        wrapper,
        "results",
        return_value=[{"title": "A", "url": "https://example.com", "content": "B"}],
    ) as mock_results:
        result = await wrapper.aresults("async query")

    assert result == [{"title": "A", "url": "https://example.com", "content": "B"}]
    mock_results.assert_called_once_with("async query")


def test_web_search_mapping_uses_harness_adapter_for_bocha_and_perplexity():
    """Research mapping should keep Bocha/Perplexity on the harness web_tools adapter."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
        BochaSearchAPIWrapper,
        PerplexitySearchAPIWrapper,
    )
    from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping

    assert search_engine_mapping["bocha"] is BochaSearchAPIWrapper
    assert search_engine_mapping["perplexity"] is PerplexitySearchAPIWrapper


def test_harness_web_search_omits_url_env_when_search_url_empty():
    """Empty search_url should let web_tools use its own defaults."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
        BochaSearchAPIWrapper,
    )

    captured_env = []

    @contextmanager
    def capture_env(values):
        captured_env.append(values)
        yield

    wrapper = BochaSearchAPIWrapper(
        search_api_key=bytearray(b"bocha-key"),
        search_url="",
        max_web_search_results=1,
    )

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "_temporary_env",
        side_effect=lambda values: capture_env(values),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "WebPaidSearchTool._bocha_search_sync",
        return_value={"provider": "bocha", "answer": "", "urls": []},
    ):
        wrapper.results("query")

    assert captured_env == [{"BOCHA_API_KEY": "bocha-key"}]


def test_harness_web_search_injects_custom_url_only_when_web_tools_can_override():
    """Configured search_url should be injected only for web_tools-overridable providers."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper import (
        BochaSearchAPIWrapper,
    )

    captured_env = []

    @contextmanager
    def capture_env(values):
        captured_env.append(values)
        yield

    wrapper = BochaSearchAPIWrapper(
        search_api_key=bytearray(b"bocha-key"),
        search_url="https://custom-bocha.example.com/search",
        max_web_search_results=1,
    )

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "_temporary_env",
        side_effect=lambda values: capture_env(values),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.harness_web_search.api_wrapper."
        "WebPaidSearchTool._bocha_search_sync",
        return_value={"provider": "bocha", "answer": "", "urls": []},
    ):
        wrapper.results("query")

    assert captured_env == [
        {
            "BOCHA_API_KEY": "bocha-key",
            "BOCHA_API_URL": "https://custom-bocha.example.com/search",
        }
    ]


def test_web_search_engine_config_accepts_all_web_search_engines():
    """Agent config should accept all registered web search engine names directly."""
    from openjiuwen_deepsearch.config.config import WebSearchEngineConfig

    for engine in ("bocha", "jina", "perplexity", "serper"):
        config = WebSearchEngineConfig(search_engine_name=engine)
        assert config.search_engine_name == engine
