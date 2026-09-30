"""MCP CLI 参数解析与配置构造测试。

覆盖三个问题:
1. streamable_http 支持 headers(经 httpx.AsyncClient 注入),非注释所述"不支持"
2. --mcp_server_name 与 --mcp_server_url 数量不匹配时报错而非静默截断
3. headers 从空 dict 改为从 --mcp_server_headers JSON 解析
"""

import argparse
import json

import pytest

from main import _build_mcp_servers_config


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(
        mcp_server_url=kwargs.get("mcp_server_url", ""),
        mcp_server_name=kwargs.get("mcp_server_name", ""),
        mcp_server_headers=kwargs.get("mcp_server_headers", ""),
        mcp_server_type=kwargs.get("mcp_server_type", "search"),
    )


def test_empty_mcp_server_url_returns_empty_list():
    args = _make_args(mcp_server_url="")
    assert _build_mcp_servers_config(args) == []


def test_headers_json_dict_applied_to_all_servers():
    """单个 JSON dict headers 适用于所有 server。"""
    headers = {"x-api-key": "secret-key"}
    args = _make_args(
        mcp_server_url="https://a.com/mcp,https://b.com/mcp",
        mcp_server_name="a,b",
        mcp_server_headers=json.dumps(headers),
    )
    config = _build_mcp_servers_config(args)
    assert len(config) == 2
    assert config[0]["headers"] == headers
    assert config[1]["headers"] == headers


def test_headers_json_list_per_server():
    """JSON 数组 headers 与 URL 一一对应。"""
    headers_list = [{"x-api-key": "key-a"}, {"x-api-key": "key-b"}]
    args = _make_args(
        mcp_server_url="https://a.com/mcp,https://b.com/mcp",
        mcp_server_name="a,b",
        mcp_server_headers=json.dumps(headers_list),
    )
    config = _build_mcp_servers_config(args)
    assert config[0]["headers"] == {"x-api-key": "key-a"}
    assert config[1]["headers"] == {"x-api-key": "key-b"}


def test_headers_json_list_length_mismatch_raises():
    """headers 数组长度与 URL 数量不匹配时报错。"""
    args = _make_args(
        mcp_server_url="https://a.com/mcp,https://b.com/mcp",
        mcp_server_name="a,b",
        mcp_server_headers=json.dumps([{"x-api-key": "only-one"}]),
    )
    with pytest.raises(ValueError, match="headers"):
        _build_mcp_servers_config(args)


def test_no_headers_yields_empty_dict():
    """不传 --mcp_server_headers 时 headers 为空 dict(向后兼容)。"""
    args = _make_args(
        mcp_server_url="https://a.com/mcp",
        mcp_server_name="a",
    )
    config = _build_mcp_servers_config(args)
    assert config[0]["headers"] == {}


def test_names_urls_length_mismatch_raises():
    """names 数量少于 urls 时报错,而非静默截断第二个 server。"""
    args = _make_args(
        mcp_server_url="https://a.com/mcp,https://b.com/mcp",
        mcp_server_name="only_one_name",
    )
    with pytest.raises(ValueError, match="数量"):
        _build_mcp_servers_config(args)


def test_names_more_than_urls_raises():
    """names 数量多于 urls 时也报错。"""
    args = _make_args(
        mcp_server_url="https://a.com/mcp",
        mcp_server_name="a,b,c",
    )
    with pytest.raises(ValueError, match="数量"):
        _build_mcp_servers_config(args)


def test_no_names_auto_generated():
    """不传 name 时按序号自动生成。"""
    args = _make_args(
        mcp_server_url="https://a.com/mcp,https://b.com/mcp",
        mcp_server_name="",
    )
    config = _build_mcp_servers_config(args)
    assert config[0]["server_name"] == "mcp_server_0"
    assert config[1]["server_name"] == "mcp_server_1"


def test_transport_type_and_type_preserved():
    args = _make_args(
        mcp_server_url="https://a.com/mcp",
        mcp_server_name="a",
        mcp_server_headers=json.dumps({"authorization": "Bearer x"}),
        mcp_server_type="extract",
    )
    config = _build_mcp_servers_config(args)
    assert config[0]["transport_type"] == "streamable_http"
    assert config[0]["type"] == "extract"
    assert config[0]["headers"] == {"authorization": "Bearer x"}
    assert config[0]["timeout"] == 30.0
