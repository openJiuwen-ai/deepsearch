# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Env → RetropusSearchAgentConfig (agent/runner path)."""

import openjiuwen_codesearch.config.env_file as env_file_mod
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
    "RETROPUS_INDEX_DIR",
    "TOKENIZE_WORKERS",
    "FEAT_ALL",
    "FEAT_BAN_TESTS",
    "FEAT_ANTI_EARLY_FINISH",
    "FEAT_SAME_FILE_EXPAND",
    "FEAT_SECOND_FILE_PROBE",
    "FEAT_INHERITS_EXPAND",
    "FEAT_EXPAND_IMPORTS",
    "FEAT_DELETE_SNIPPETS",
)


def _isolate_env(monkeypatch, tmp_path, *, contents: str = "") -> None:
    """Point the dotenv loader at a temp cwd and clear Retropus knobs."""
    (tmp_path / ".env").write_text(contents, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_file_mod, "_LOADED", False)
    for key in _RETROPUS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_retropus_agent_config_from_env_reads_loop_and_feat_flags(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_TOOL_CALLS", "7")
    monkeypatch.setenv("MAX_ROUNDS", "3")
    monkeypatch.setenv("MAX_FINAL_SPANS", "9")
    monkeypatch.setenv("MIN_SPANS_BEFORE_FINISH", "5")
    monkeypatch.setenv("FEAT_ALL", "0")
    monkeypatch.setenv("FEAT_BAN_TESTS", "1")
    monkeypatch.setenv("FEAT_ANTI_EARLY_FINISH", "1")
    monkeypatch.setenv("FEAT_SAME_FILE_EXPAND", "1")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.max_tool_calls == 7
    assert cfg.max_rounds == 3
    assert cfg.max_final_spans == 9
    assert cfg.min_spans_before_finish == 5
    assert cfg.feat_ban_tests is True
    assert cfg.feat_anti_early_finish is True
    assert cfg.feat_same_file_expand is True
    assert cfg.feat_inherits_expand is False
    assert cfg.feat_expand_imports is False
    assert cfg.feat_delete_snippets is False


def test_retropus_agent_config_reads_feat_expand_imports(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FEAT_EXPAND_IMPORTS", "1")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.feat_expand_imports is True


def test_retropus_agent_config_reads_feat_delete_snippets(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FEAT_DELETE_SNIPPETS", "1")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.feat_delete_snippets is True


def test_codesearch_config_from_env_populates_retropus(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CODESEARCH_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CODESEARCH_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("MAX_ROUNDS", "4")
    monkeypatch.setenv("FEAT_BAN_TESTS", "1")

    config = CodeSearchConfig.from_env()
    assert config.retropus.max_rounds == 4
    assert config.retropus.feat_ban_tests is True


def test_from_env_dotenv_overrides_export(monkeypatch, tmp_path):
    """``.env`` wins over ``export``; mirrors ``ensure_dotenv_loaded(override=True)``."""
    _isolate_env(
        monkeypatch,
        tmp_path,
        contents="MAX_ROUNDS=99\nFEAT_BAN_TESTS=1\n",
    )
    monkeypatch.setenv("MAX_ROUNDS", "2")
    monkeypatch.setenv("FEAT_BAN_TESTS", "0")

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.max_rounds == 99
    assert cfg.feat_ban_tests is True


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


def test_config_reads_retropus_index_dir_env(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RETROPUS_INDEX_DIR", str(tmp_path / "idx"))

    cfg = RetropusSearchAgentConfig.from_env()
    assert cfg.index_dir == str(tmp_path / "idx")
