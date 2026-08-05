# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeSearchConfig.from_env()：仅 CODESEARCH_LLM_API_KEY / CODESEARCH_LLM_BASE_URL。"""

import pytest

from openjiuwen_codesearch.config.config import CodeSearchConfig


def _key(cfg: CodeSearchConfig) -> str:
    return bytes(cfg.llm.main.api_key).decode("utf-8")


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for key in (
        "CODESEARCH_LLM_API_KEY",
        "CODESEARCH_LLM_BASE_URL",
        "CODESEARCH_LLM_MODEL",
        "CODESEARCH_FILTER_LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_from_env_reads_api_key_and_base_url(monkeypatch):
    monkeypatch.setenv("CODESEARCH_LLM_API_KEY", "cs-key")
    monkeypatch.setenv("CODESEARCH_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("CODESEARCH_LLM_MODEL", "main-model")
    monkeypatch.setenv("CODESEARCH_FILTER_LLM_MODEL", "filter-model")

    cfg = CodeSearchConfig.from_env()
    assert _key(cfg) == "cs-key"
    assert bytes(cfg.llm.filter.api_key).decode("utf-8") == "cs-key"
    assert cfg.llm.main.base_url == "https://api.example.com/v1"
    assert cfg.llm.filter.base_url == "https://api.example.com/v1"
    assert cfg.llm.main.model_name == "main-model"
    assert cfg.llm.filter.model_name == "filter-model"


def test_from_env_base_url_defaults_empty(monkeypatch):
    monkeypatch.setenv("CODESEARCH_LLM_API_KEY", "k")
    cfg = CodeSearchConfig.from_env()
    assert cfg.llm.main.base_url == ""
    assert _key(cfg) == "k"


def test_from_env_missing_key_is_empty(monkeypatch):
    cfg = CodeSearchConfig.from_env()
    assert _key(cfg) == ""
    assert cfg.llm.main.base_url == ""
