"""Config redaction for SearchFinalResult logging."""

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.utils import (
    SaveSearchFinalResultConfig,
    Termination,
    _save_and_return_search_final_result,
    anonymize_config_for_logging,
    redact_urls_in_text,
)


def test_anonymize_nested_api_keys_and_bytearray():
    cfg = {
        "llm_config": {
            "general": {
                "api_key": bytearray(b"sk-secret"),
                "model_name": "x",
            }
        },
        "web_fetch_provider_config": {"provider_name": "jina", "api_key": "fetch-plain"},
        "search_workflow_milvus_config": {"embedder_api_key": "emb"},
        "validator_agent": {"llm_config": {"general": {"api_key": "v"}}},
        "headers": [{"name": "Authorization", "value": "Bearer x"}, {"name": "X-Other", "value": "ok"}],
    }
    out = anonymize_config_for_logging(cfg)
    assert out["llm_config"]["general"]["api_key"] == "***"
    assert out["llm_config"]["general"]["model_name"] == "x"
    assert out["web_fetch_provider_config"]["api_key"] == "***"
    assert out["search_workflow_milvus_config"]["embedder_api_key"] == "***"
    assert out["validator_agent"]["llm_config"]["general"]["api_key"] == "***"
    assert out["headers"][0]["value"] == "***"
    assert out["headers"][1]["value"] == "ok"
    # original unchanged
    assert cfg["web_fetch_provider_config"]["api_key"] == "fetch-plain"


def test_save_search_final_result_uses_redacted_config():
    raw = {"llm_config": {"general": {"api_key": "must-not-leak"}}}
    result = _save_and_return_search_final_result(
        SaveSearchFinalResultConfig(
            question="q",
            termination=Termination.FAIL_LIMIT,
            messages=[],
            prediction=None,
            params={"start_time": 0.0},
            config=raw,
        )
    )
    assert result.config["llm_config"]["general"]["api_key"] == "***"
    assert raw["llm_config"]["general"]["api_key"] == "must-not-leak"


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "password",
        "client_secret",
        "search_api_key",
    ],
)
def test_anonymize_misc_secret_keys(key):
    assert anonymize_config_for_logging({key: "x"})[key] == "***"


# --- MCP 凭据脱敏回归测试 ---
# 以下三个测试覆盖曾发生的凭据泄漏:Proxy-Authorization / X-Auth header
# 在普通 dict 中未被脱敏,以及 server_url query param 中的 API key 未被扫描。


def test_anonymize_redacts_proxy_authorization_in_plain_dict_header():
    """Proxy-Authorization 作为普通 dict key 时必须脱敏(非 {name,value} 结构)。"""
    cfg = {
        "mcp_servers": [
            {"headers": {"Proxy-Authorization": "Bearer proxy-secret-token"}}
        ]
    }
    out = anonymize_config_for_logging(cfg)
    assert out["mcp_servers"][0]["headers"]["Proxy-Authorization"] == "***"


def test_anonymize_redacts_x_auth_in_plain_dict_header():
    """X-Auth 作为普通 dict key 时必须脱敏。"""
    cfg = {
        "mcp_servers": [
            {"headers": {"X-Auth": "x-auth-secret-value"}}
        ]
    }
    out = anonymize_config_for_logging(cfg)
    assert out["mcp_servers"][0]["headers"]["X-Auth"] == "***"


def test_anonymize_redacts_api_key_in_server_url_query_param():
    """server_url query param 中疑似凭据的 value 必须替换为 ***。"""
    cfg = {
        "mcp_servers": [
            {
                "server_url": "https://mcp.tavily.com/mcp/?tavilyApiKey=SECRET_KEY_IN_URL",
                "transport_type": "streamable_http",
            }
        ]
    }
    out = anonymize_config_for_logging(cfg)
    redacted_url = out["mcp_servers"][0]["server_url"]
    assert "SECRET_KEY_IN_URL" not in redacted_url
    assert "tavilyApiKey=" in redacted_url  # param 名保留,value 被替换
    # 非 sensitive 的 query param 保留原值
    cfg2 = {
        "mcp_servers": [
            {"server_url": "https://example.com/mcp/?foo=bar&apiKey=SECRET"}
        ]
    }
    out2 = anonymize_config_for_logging(cfg2)
    url2 = out2["mcp_servers"][0]["server_url"]
    assert "SECRET" not in url2
    assert "foo=bar" in url2


# --- redact_urls_in_text: 从异常消息等自由文本中脱敏 URL query param ---


def test_redact_urls_in_text_redacts_secret_query_params():
    """文本中 URL query param 疑似凭据 value 替换为 ***。"""
    text = "ConnectError: https://mcp.tavily.com/mcp/?tavilyApiKey=SECRET_IN_URL"
    redacted = redact_urls_in_text(text)
    assert "SECRET_IN_URL" not in redacted
    assert "tavilyApiKey=***" in redacted
    assert "ConnectError:" in redacted  # 非 URL 部分保留


def test_redact_urls_in_text_preserves_non_secret_params():
    """非敏感 query param 保留原值。"""
    text = "Failed: https://example.com/mcp/?foo=bar&api_key=SECRET"
    redacted = redact_urls_in_text(text)
    assert "foo=bar" in redacted
    assert "SECRET" not in redacted


def test_redact_urls_in_text_no_url_unchanged():
    """文本中无 URL 时原样返回。"""
    assert redact_urls_in_text("Connection refused") == "Connection refused"


def test_redact_urls_in_text_multiple_urls():
    """文本中多个 URL 均被脱敏。"""
    text = "https://a.com/?key=SECRET_A and https://b.com/?token=SECRET_B"
    redacted = redact_urls_in_text(text)
    assert "SECRET_A" not in redacted
    assert "SECRET_B" not in redacted
    assert "key=***" in redacted
    assert "token=***" in redacted
