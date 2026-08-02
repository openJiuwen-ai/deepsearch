# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Env → RetropusSearchAgentConfig (agent/runner path)."""

import os

import openjiuwen_codesearch.config.config as cfg_mod
from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.config.config import CodeSearchConfig

_RETROPUS_ENV_KEYS = (
    "MAX_TOOL_CALLS",
    "MAX_ROUNDS",
    "MAX_FINAL_SPANS",
    "MAX_OBS_CHARS",
    "MAX_READ_LINES",
    "MIN_SPANS_BEFORE_FINISH",
    "MIN_FILES_BEFORE_FINISH",
    "MIN_MANDATORY_RETURN_SPANS",
    "RETROPUS_MIN_MANDATORY_RETURN_SPANS",
    "RETRIEVER",
    "TOKENIZE_WORKERS",
    "IMP_ALL",
    "IMP_BAN_TESTS",
    "IMP_ANTI_EARLY_FINISH",
    "IMP_SAME_FILE_EXPAND",
    "IMP_SECOND_FILE_PROBE",
    "IMP_INHERITS_EXPAND",
    "IMP_EXPAND_IMPORTS",
)


def _isolate_env(monkeypatch, tmp_path, *, contents: str = "") -> None:
    """Point dotenv loader at a temp file and clear Retropus knobs."""
    env_file = tmp_path / ".env"
    env_file.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "_DEFAULT_ENV_FILE", env_file)
    monkeypatch.setattr(cfg_mod, "_DOTENV_LOADED", False)
    for key in _RETROPUS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_retropus_agent_config_from_env_reads_loop_and_imp_flags(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_TOOL_CALLS", "7")
    monkeypatch.setenv("MAX_ROUNDS", "3")
    monkeypatch.setenv("MAX_FINAL_SPANS", "9")
    monkeypatch.setenv("MIN_SPANS_BEFORE_FINISH", "5")
    monkeypatch.setenv("IMP_ALL", "0")
    monkeypatch.setenv("IMP_BAN_TESTS", "1")
    monkeypatch.setenv("IMP_ANTI_EARLY_FINISH", "1")
    monkeypatch.setenv("IMP_SAME_FILE_EXPAND", "1")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.max_tool_calls == 7
    assert cfg.max_rounds == 3
    assert cfg.max_final_spans == 9
    assert cfg.min_spans_before_finish == 5
    assert cfg.imp_ban_tests is True
    assert cfg.imp_anti_early_finish is True
    assert cfg.imp_same_file_expand is True
    assert cfg.imp_inherits_expand is False
    assert cfg.imp_expand_imports is False


def test_retropus_agent_config_reads_imp_expand_imports(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("IMP_EXPAND_IMPORTS", "1")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.imp_expand_imports is True


def test_codesearch_config_from_env_populates_retropus(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MAX_ROUNDS", "4")
    monkeypatch.setenv("IMP_BAN_TESTS", "1")

    config = CodeSearchConfig.from_env()
    assert config.retropus.max_rounds == 4
    assert config.retropus.imp_ban_tests is True


def test_from_env_respects_existing_os_environ_over_dotenv(monkeypatch, tmp_path):
    """Process env wins; mirrors CodeSearchConfig._load_dotenv(override=False)."""
    _isolate_env(
        monkeypatch,
        tmp_path,
        contents="MAX_ROUNDS=99\nIMP_BAN_TESTS=1\n",
    )
    monkeypatch.setenv("MAX_ROUNDS", "2")
    monkeypatch.setenv("IMP_BAN_TESTS", "0")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.max_rounds == 2
    assert cfg.imp_ban_tests is False
    assert os.environ["MAX_ROUNDS"] == "2"


def test_config_reads_min_mandatory_return_spans_env(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MIN_MANDATORY_RETURN_SPANS", "4")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.min_mandatory_return_spans == 4


def test_config_reads_retropus_prefixed_mandatory_env(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RETROPUS_MIN_MANDATORY_RETURN_SPANS", "2")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.min_mandatory_return_spans == 2


def test_config_reads_tokenize_workers_env(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENIZE_WORKERS", "3")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.tokenize_workers == 3


def test_config_reads_obs_and_read_limits_env(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_OBS_CHARS", "1200")
    monkeypatch.setenv("MAX_READ_LINES", "80")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.max_obs_chars == 1200
    assert cfg.max_read_lines == 80
