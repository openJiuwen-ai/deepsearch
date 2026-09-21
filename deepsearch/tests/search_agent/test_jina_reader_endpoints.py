# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import Mock, patch

import requests

from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper import (
    JinaWebFetchProvider,
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
    fetch = JinaWebFetchProvider(api_key="test-key")
    china_resp = Mock(status_code=200, text="from-china")
    called_urls: list[str] = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        if url.startswith("https://r.jinaai.cn/"):
            return china_resp
        raise AssertionError(f"unexpected url: {url}")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        side_effect=fake_get,
    ):
        assert fetch._read_via_jina("https://example.com") == "from-china"
    assert any(url.startswith("https://r.jinaai.cn/") for url in called_urls)
    assert any(url.startswith("https://r.jina.ai/") for url in called_urls)


def test_web_fetch_uses_fastest_successful_reader_endpoint():
    fetch = JinaWebFetchProvider(api_key="test-key")
    china_resp = Mock(status_code=200, text="from-china")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        return_value=china_resp,
    ) as mock_get:
        assert fetch._read_via_jina("https://example.com") == "from-china"
        assert mock_get.call_count == len(fetch._reader_bases)


def test_web_fetch_falls_back_to_global_reader_endpoint():
    fetch = JinaWebFetchProvider(api_key="test-key")
    global_resp = Mock(status_code=200, text="from-global")

    def fake_get(url, **kwargs):
        if url.startswith("https://r.jinaai.cn/"):
            raise requests.exceptions.ConnectTimeout("blocked")
        if url.startswith("https://r.jina.ai/"):
            return global_resp
        raise AssertionError(f"unexpected url: {url}")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        side_effect=fake_get,
    ):
        assert fetch._read_via_jina("https://example.com") == "from-global"


def test_web_fetch_logs_summary_when_every_endpoint_rejects_credentials(caplog):
    """全 base 返 401/403 时必须留下汇总日志。

    回归用：原先只有 RequestException 才设置 last_error，汇总日志因此被跳过；
    "所有 base 都被拦下"（配错 key / 匿名被 Cloudflare 拦）这种最常见场景反而没有汇总。
    """
    fetch = JinaWebFetchProvider(api_key="test-key")
    forbidden = Mock(status_code=403, text="Just a moment...")

    def fake_get(url, **kwargs):
        return forbidden

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        side_effect=fake_get,
    ), caplog.at_level("WARNING"):
        assert fetch._read_via_jina("https://example.com") == "[web_fetch] Failed to read page."

    assert "all Jina reader endpoints failed" in caplog.text
    assert "https://example.com" in caplog.text
    # 每个 base 的原因都要出现在汇总里
    for base in fetch._reader_bases:
        assert base in caplog.text


def test_web_fetch_summary_reports_http_status_for_non_auth_failures(caplog):
    """非鉴权类的 HTTP 失败（如 500）同样要进汇总，且不带 auth rejected 标注。"""
    fetch = JinaWebFetchProvider(api_key="test-key")
    server_error = Mock(status_code=500, text="boom")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        return_value=server_error,
    ), caplog.at_level("WARNING"):
        assert fetch._read_via_jina("https://example.com") == "[web_fetch] Failed to read page."

    assert "all Jina reader endpoints failed" in caplog.text
    assert "HTTP 500" in caplog.text
    assert "auth rejected" not in caplog.text
