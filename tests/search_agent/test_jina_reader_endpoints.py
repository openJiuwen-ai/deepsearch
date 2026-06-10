# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import Mock, patch

import requests

from openjiuwen_deepsearch.algorithm.search_tools.web_fetch_tool import (
    WebFetch,
    build_jina_reader_url,
    resolve_jina_reader_base_urls,
)


def test_resolve_jina_reader_base_urls_prefers_china_mirror(monkeypatch):
    monkeypatch.delenv("JINA_READER_BASE_URL", raising=False)
    bases = resolve_jina_reader_base_urls()
    assert bases[0] == "https://r.jinaai.cn"
    assert "https://r.jina.ai" in bases


def test_resolve_jina_reader_base_urls_honors_env_override(monkeypatch):
    monkeypatch.setenv("JINA_READER_BASE_URL", "https://custom.reader.example")
    bases = resolve_jina_reader_base_urls()
    assert bases[0] == "https://custom.reader.example"
    assert "https://r.jinaai.cn" in bases


def test_build_jina_reader_url():
    assert build_jina_reader_url("https://r.jinaai.cn", "https://example.com") == (
        "https://r.jinaai.cn/https://example.com"
    )


def test_web_fetch_races_reader_endpoints_in_parallel():
    fetch = WebFetch({"jina_api_key": "test-key"})
    china_resp = Mock(status_code=200, text="from-china")
    called_urls: list[str] = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        if url.startswith("https://r.jinaai.cn/"):
            return china_resp
        raise AssertionError(f"unexpected url: {url}")

    with patch(
        "openjiuwen_deepsearch.algorithm.search_tools.web_fetch_tool.requests.get",
        side_effect=fake_get,
    ):
        assert fetch._read_via_jina("https://example.com") == "from-china"
    assert any(url.startswith("https://r.jinaai.cn/") for url in called_urls)
    assert any(url.startswith("https://r.jina.ai/") for url in called_urls)


def test_web_fetch_uses_fastest_successful_reader_endpoint():
    fetch = WebFetch({"jina_api_key": "test-key"})
    china_resp = Mock(status_code=200, text="from-china")

    with patch(
        "openjiuwen_deepsearch.algorithm.search_tools.web_fetch_tool.requests.get",
        return_value=china_resp,
    ) as mock_get:
        assert fetch._read_via_jina("https://example.com") == "from-china"
        assert mock_get.call_count == len(fetch._jina_reader_bases)


def test_web_fetch_falls_back_to_global_reader_endpoint():
    fetch = WebFetch({"jina_api_key": "test-key"})
    global_resp = Mock(status_code=200, text="from-global")

    def fake_get(url, **kwargs):
        if url.startswith("https://r.jinaai.cn/"):
            raise requests.exceptions.ConnectTimeout("blocked")
        if url.startswith("https://r.jina.ai/"):
            return global_resp
        raise AssertionError(f"unexpected url: {url}")

    with patch(
        "openjiuwen_deepsearch.algorithm.search_tools.web_fetch_tool.requests.get",
        side_effect=fake_get,
    ):
        assert fetch._read_via_jina("https://example.com") == "from-global"
