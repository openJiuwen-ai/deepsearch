from __future__ import annotations

import sys
from types import SimpleNamespace

import openjiuwen_codesearch.server.main as main_mod


def _capture_uvicorn_run(monkeypatch):
    captured = {}
    fake_uvicorn = SimpleNamespace(run=lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return captured


def test_invalid_log_level_falls_back_to_info(monkeypatch):
    """CODESEARCH_LOG_LEVEL 非法时不应把原值传给 uvicorn（否则 KeyError 崩启动）。"""
    captured = _capture_uvicorn_run(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "log_level", "verbose")

    main_mod.main()

    assert captured["log_level"] == "info"


def test_valid_log_level_is_forwarded(monkeypatch):
    captured = _capture_uvicorn_run(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "log_level", "DEBUG")

    main_mod.main()

    assert captured["log_level"] == "debug"
