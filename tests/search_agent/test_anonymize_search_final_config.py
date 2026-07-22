"""Config redaction for SearchFinalResult logging."""

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.utils import (
    SaveSearchFinalResultConfig,
    Termination,
    _save_and_return_search_final_result,
    anonymize_config_for_logging,
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
