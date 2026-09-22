# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.agc_ainetworking.api_wrapper import (
    AgcAiNetworkingSearchAPIWrapper,
)

MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.agc_ainetworking.api_wrapper"


def _make_payload(rows):
    """Build a fake AGC AI Networking success response payload."""
    return {"code": 0, "msg": "success", "webResult": rows}


# ---- Scenario 1: sync happy path -------------------------------------------------

def test_sync_results_normalize_web_result_rows():
    """sync results() should normalize webResult[] (chunk→content, published ISO, site_name)."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"agc-key"),
        search_url="",
    )
    payload = _make_payload([
        {
            "title": "Title One",
            "url": "https://example.com/a",
            "chunk": "chunk body",
            "publishTime": "1700000000",
            "siteName": "Example",
        },
        {
            "title": "Title Two",
            "url": "https://example.com/b",
            "content": "fallback content",
            "publishTime": "1600000000",
        },
    ])
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = payload

    with patch(f"{MODULE_PATH}.requests.post", return_value=mock_response):
        result = wrapper.results("query")

    assert len(result) == 2
    assert result[0] == {
        "title": "Title One",
        "url": "https://example.com/a",
        "content": "chunk body",
        "source": "agc_ainetworking",
        "published": "2023-11-14",
        "site_name": "Example",
    }
    # row 2: no chunk (content fallback), no siteName
    assert result[1]["content"] == "fallback content"
    assert result[1]["published"] == "2020-09-13"
    assert "site_name" not in result[1]


# ---- Scenario 2: async happy path ------------------------------------------------

@pytest.mark.asyncio
async def test_async_results_normalize_web_result_rows():
    """aresults() should normalize webResult[] like sync path."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"agc-key"),
        search_url="",
    )
    payload = _make_payload([
        {
            "title": "Async Title",
            "url": "https://example.com/async",
            "chunk": "async chunk",
            "publishTime": "1700000000",
            "siteName": "AsyncSite",
        },
    ])

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = payload

    mock_post_context = AsyncMock()
    mock_post_context.__aenter__.return_value = mock_response
    mock_post_context.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.post.return_value = mock_post_context

    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_context.__aexit__.return_value = None

    with patch(f"{MODULE_PATH}.aiohttp.ClientSession", return_value=mock_session_context), \
            patch(f"{MODULE_PATH}.SslUtils.get_ssl_config", return_value=(False, "")):
        result = await wrapper.aresults("query")

    assert result == [
        {
            "title": "Async Title",
            "url": "https://example.com/async",
            "content": "async chunk",
            "source": "agc_ainetworking",
            "published": "2023-11-14",
            "site_name": "AsyncSite",
        }
    ]


# ---- Scenario 3: request body assertions ----------------------------------------

def test_request_body_count_clamp_and_omits_empty_sites_category():
    """count clamped to [1, 50]; empty sites/category → keys absent."""
    # 0 → 1
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="", max_web_search_results=0,
    )
    assert wrapper._build_request_body("q")["count"] == 1
    # 100 → 50
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="", max_web_search_results=100,
    )
    body = wrapper._build_request_body("q")
    assert body["count"] == 50
    assert "sites" not in body
    assert "category" not in body
    assert body["freshness"] == "noLimit"


def test_request_body_extension_applies_sites_category_freshness():
    """extension config: sites ≤20, category/freshness effective."""
    many_sites = [f"{i}.example.com" for i in range(25)]
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"),
        search_url="",
        extension={
            "sites": many_sites,
            "category": ["  tech  ", "", "news"],
            "freshness": "oneDay",
        },
    )
    body = wrapper._build_request_body("q")
    # normalize_domains strips www; here no www so just dedup
    assert len(body["sites"]) == 20
    assert body["sites"] == many_sites[:20]
    assert body["category"] == ["tech", "news"]
    assert body["freshness"] == "oneDay"


# ---- Scenario 4: request headers contain X-Api-Key -------------------------------

def test_request_headers_contain_x_api_key():
    """Headers should include X-Api-Key when key is set."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"secret-key"), search_url="",
    )
    headers = wrapper._build_headers()
    assert headers["X-Api-Key"] == "secret-key"
    assert headers["Content-Type"] == "application/json"


def test_request_headers_omit_x_api_key_when_empty():
    """Empty key → no X-Api-Key header (server 401 fallback)."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(search_api_key="", search_url="")
    headers = wrapper._build_headers()
    assert "X-Api-Key" not in headers


# ---- Scenario 5: code != 0 -------------------------------------------------------

def test_sync_code_nonzero_raises_runtime_error():
    """sync code != 0 → RuntimeError."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="",
    )
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"code": 102010, "msg": "permission denied"}

    with patch(f"{MODULE_PATH}.requests.post", return_value=mock_response), \
            pytest.raises(RuntimeError, match="code=102010"):
        wrapper.results("query")


@pytest.mark.asyncio
async def test_async_code_nonzero_returns_empty():
    """async code != 0 → soft-fail returns []."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="",
    )
    payload = {"code": 10300, "msg": "internal error"}

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = payload

    mock_post_context = AsyncMock()
    mock_post_context.__aenter__.return_value = mock_response
    mock_post_context.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.post.return_value = mock_post_context

    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_context.__aexit__.return_value = None

    with patch(f"{MODULE_PATH}.aiohttp.ClientSession", return_value=mock_session_context), \
            patch(f"{MODULE_PATH}.SslUtils.get_ssl_config", return_value=(False, "")):
        result = await wrapper.aresults("query")

    assert result == []


# ---- Scenario 6: empty query ----------------------------------------------------

def test_empty_query_returns_empty_list_sync():
    """Empty query → [] without HTTP call."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="",
    )
    with patch(f"{MODULE_PATH}.requests.post") as mock_post:
        assert wrapper.results("") == []
        assert wrapper.results("   ") == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_empty_query_returns_empty_list_async():
    """Empty query → [] without HTTP call (async)."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="",
    )
    with patch(f"{MODULE_PATH}.aiohttp.ClientSession") as mock_session:
        assert await wrapper.aresults("") == []
        assert await wrapper.aresults("   ") == []
        mock_session.assert_not_called()


# ---- Scenario 7: content three-state fallback -----------------------------------

def test_content_three_state_fallback():
    """chunk present → use chunk; chunk empty + content present → content; both empty → ''."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"), search_url="",
    )
    payload = _make_payload([
        {"title": "r1", "url": "https://a.com/1", "chunk": "chunk-body", "content": "ignored"},
        {"title": "r2", "url": "https://a.com/2", "chunk": "", "content": "content-body"},
        {"title": "r3", "url": "https://a.com/3"},
    ])
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = payload

    with patch(f"{MODULE_PATH}.requests.post", return_value=mock_response):
        result = wrapper.results("q")

    assert len(result) == 3
    assert result[0]["content"] == "chunk-body"
    assert result[1]["content"] == "content-body"
    assert result[2]["content"] == ""


# ---- Scenario 8: publishTime "0"/invalid → no published key ----------------------
def test_publishtime_zero_or_invalid_omits_published():
    """publishTime '0'/invalid/missing → no published key."""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"),
        search_url="",
    )
    payload = _make_payload([
        {"title": "r1", "url": "https://a.com/1", "publishTime": "0"},
        {"title": "r2", "url": "https://a.com/2", "publishTime": "not-a-number"},
        {"title": "r3", "url": "https://a.com/3"},
    ])
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = payload

    with patch(f"{MODULE_PATH}.requests.post", return_value=mock_response):
        result = wrapper.results("q")

    assert len(result) == 3
    for row in result:
        assert "published" not in row


# ---- Scenario 8b: 超大整数 publishTime 不崩溃 ---------------------------------

def test_publishtime_huge_integer_does_not_crash():
    """超大整数 publishTime 不抛异常，跳过该条 published 键，不影响同批其他记录。"""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"),
        search_url="",
    )
    payload = _make_payload([
        {"title": "bad", "url": "https://a.com/bad", "publishTime": "99999999999999999"},
        {"title": "good", "url": "https://a.com/good", "publishTime": "1700000000"},
    ])
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = payload

    with patch(f"{MODULE_PATH}.requests.post", return_value=mock_response):
        result = wrapper.results("q")

    assert len(result) == 2
    # 超大整数的该条记录：无 published 键，其余字段正常
    assert "published" not in result[0]
    assert result[0]["title"] == "bad"
    assert result[0]["url"] == "https://a.com/bad"
    # 同批正常记录不受影响
    assert result[1]["published"] == "2023-11-14"


# ---- Scenario 9: sites 统一剥离 www. 前缀（共享 normalize_domains 行为） ---------------

def test_extension_sites_strips_www_prefix():
    """extension.sites 带 www. 前缀时,wrapper.sites 统一剥离 www.。"""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"),
        search_url="",
        extension={"sites": ["www.huawei.com"]},
    )
    assert wrapper.sites == ["huawei.com"]


def test_extension_sites_normalizes_scheme_case_port_but_strips_www():
    """sites 清洗 scheme/大小写/端口/去重,并剥离 www. 前缀。"""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"),
        search_url="",
        extension={"sites": ["https://WWW.Huawei.com:443", "www.huawei.com"]},
    )
    # 去重 + 清洗后保留一个,无 www. 前缀
    assert wrapper.sites == ["huawei.com"]


def test_request_body_sites_strips_www_prefix():
    """发给华为端的请求体 sites 字段为剥离 www. 后的域名。"""
    wrapper = AgcAiNetworkingSearchAPIWrapper(
        search_api_key=bytearray(b"k"),
        search_url="",
        extension={"sites": ["www.huawei.com"]},
    )
    body = wrapper._build_request_body("q")
    assert body["sites"] == ["huawei.com"]
