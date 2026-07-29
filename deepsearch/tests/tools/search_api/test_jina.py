# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, Mock, patch

import pytest


def test_jina_search_results_uses_default_url_and_json_body():
    """Jina should call the normal s.jina.ai Search API when search_url is empty."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper import (
        JinaSearchAPIWrapper,
    )

    wrapper = JinaSearchAPIWrapper(
        search_api_key=bytearray(b"jina-key"),
        search_url="",
        max_web_search_results=3,
        extension={"gl": "us", "hl": "en", "location": "San Francisco", "page": 2},
    )

    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"code": 200, "status": 20000, "data": []}

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper.requests.post",
        return_value=mock_response,
    ) as mock_post:
        result = wrapper.results("test query")

    assert result == []
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert call_args[0][0] == "https://s.jina.ai/"
    assert call_args[1]["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer jina-key",
    }
    assert call_args[1]["json"] == {
        "q": "test query",
        "num": 3,
        "gl": "us",
        "hl": "en",
        "location": "San Francisco",
        "page": 2,
    }


def test_jina_search_results_omits_authorization_when_key_empty():
    """Empty Jina API key should not add an empty Authorization header."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper import (
        JinaSearchAPIWrapper,
    )

    wrapper = JinaSearchAPIWrapper(search_api_key=bytearray(b""), search_url="")

    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"data": []}

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper.requests.post",
        return_value=mock_response,
    ) as mock_post:
        wrapper.results("query")

    assert "Authorization" not in mock_post.call_args[1]["headers"]


def test_jina_search_results_normalize_content_and_skip_invalid_rows():
    """Jina response data should normalize content fields for research collectors."""
    from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH, MAX_URL_LENGTH
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper import (
        JinaSearchAPIWrapper,
    )

    wrapper = JinaSearchAPIWrapper(search_api_key=bytearray(b"k"), search_url="https://custom.jina.example")
    long_title = "T" * (MAX_SEARCH_CONTENT_LENGTH + 10)
    long_url = "https://example.com/" + ("u" * MAX_URL_LENGTH)
    long_content = "C" * (MAX_SEARCH_CONTENT_LENGTH + 10)
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "data": [
            {"title": long_title, "url": long_url, "content": long_content, "description": "desc"},
            {"title": "Fallback", "url": "https://example.com/fallback", "description": "fallback desc"},
            {"title": "No URL", "content": "skip me"},
            "not a row",
        ]
    }

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper.requests.post",
        return_value=mock_response,
    ):
        result = wrapper.results("query")

    assert len(result) == 2
    assert len(result[0]["title"]) == MAX_SEARCH_CONTENT_LENGTH
    assert len(result[0]["url"]) == MAX_URL_LENGTH
    assert len(result[0]["content"]) == MAX_SEARCH_CONTENT_LENGTH
    assert result[0]["source"] == "jina"
    assert result[1] == {
        "title": "Fallback",
        "url": "https://example.com/fallback",
        "content": "fallback desc",
        "source": "jina",
    }


@pytest.mark.asyncio
async def test_jina_search_results_async_uses_same_request_shape():
    """Async Jina search should use the same endpoint, headers, and payload."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper import (
        JinaSearchAPIWrapper,
    )

    wrapper = JinaSearchAPIWrapper(
        search_api_key=bytearray(b"jina-key"),
        search_url="",
        max_web_search_results=2,
    )

    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {
        "data": [{"title": "Async", "url": "https://example.com", "content": "body"}]
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = mock_response

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await wrapper.aresults("async query")

    assert result == [
        {"title": "Async", "url": "https://example.com", "content": "body", "source": "jina"}
    ]
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://s.jina.ai/"
    assert call_args[1]["json"] == {"q": "async query", "num": 2}


def test_web_search_mapping_uses_direct_jina_wrapper():
    """Research mapping should route jina to the direct s.jina.ai wrapper."""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.jina.api_wrapper import (
        JinaSearchAPIWrapper,
    )
    from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping

    assert search_engine_mapping["jina"] is JinaSearchAPIWrapper
