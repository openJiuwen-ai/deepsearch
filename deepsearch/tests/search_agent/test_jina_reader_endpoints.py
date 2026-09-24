# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import threading
from unittest.mock import Mock, patch

import pytest
import requests

from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper import (
    JinaWebFetchProvider,
    build_jina_reader_url,
    resolve_jina_reader_base_urls,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


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


def test_web_fetch_summary_hides_url_in_sensitive_mode(caplog):
    """敏感模式下汇总日志不得出现目标 URL 或各 base 的失败详情。

    回归用：汇总日志最初直接打印 target url，而本模块的 logger 不走
    webpage_enrichment 的 _log_fetch_event 脱敏，会把 URL 落盘。
    """
    fetch = JinaWebFetchProvider(api_key="test-key")
    forbidden = Mock(status_code=403, text="Just a moment...")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        return_value=forbidden,
    ), patch.object(LogManager, "is_sensitive", return_value=True), caplog.at_level("WARNING"):
        assert fetch._read_via_jina("https://secret.example/private") == "[web_fetch] Failed to read page."

    assert "all Jina reader endpoints failed" in caplog.text
    assert "secret.example" not in caplog.text
    assert "auth rejected" not in caplog.text


def _racing_get_rejected_first(rejected: Mock, mirror_ok: Mock):
    """构造一个顺序确定的竞速：被拒的 base 先完成，可用的 base 后完成。

    as_completed 的完成顺序在测试里本来就是不确定的；这里强制"被拒的先回"，
    才能稳定复现"最快返回的 base 把整次请求短路掉"这一回归。
    """
    rejected_done = threading.Event()

    def fake_get(url, **kwargs):
        if url.startswith("https://r.jina.ai/"):
            rejected_done.set()
            return rejected
        assert rejected_done.wait(timeout=5), "被拒的 base 未先完成，测试失去意义"
        return mirror_ok

    return fake_get


@pytest.mark.parametrize("rejected_status", [401, 403])
def test_web_fetch_does_not_short_circuit_on_credential_rejection(rejected_status, caplog):
    """某个 base 返 401/403 时不得结束竞速。

    回归用：原先鉴权失败直接 return，而官方 base 对本机请求回得最快，
    于是整次请求被它抢先结束，镜像能返回的 200 正文反而拿不到。
    """
    fetch = JinaWebFetchProvider(api_key="test-key")
    rejected = Mock(status_code=rejected_status, text="Just a moment...")
    mirror_ok = Mock(status_code=200, text="from-mirror")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        side_effect=_racing_get_rejected_first(rejected, mirror_ok),
    ), caplog.at_level("WARNING"):
        assert fetch._read_via_jina("https://example.com") == "from-mirror"

    assert "all Jina reader endpoints failed" not in caplog.text


def test_fetch_page_returns_mirror_content_when_official_endpoint_rejects():
    """端到端：官方 base 被拒时 fetch_page 仍应拿到镜像正文，且无需重试。"""
    fetch = JinaWebFetchProvider(api_key="test-key")
    rejected = Mock(status_code=403, text="Just a moment...")
    mirror_ok = Mock(status_code=200, text="from-mirror")

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper.requests.get",
        side_effect=_racing_get_rejected_first(rejected, mirror_ok),
    ) as mock_get:
        assert fetch.fetch_page("https://example.com") == "from-mirror"

    assert mock_get.call_count == len(fetch._reader_bases)
