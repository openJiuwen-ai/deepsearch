# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""Shared fixtures for search_api wrapper tests.

Many wrapper tests use fake hostnames (e.g. ``petal.example.com``) that do not
resolve, so ``_resolved_search_url`` SSRF validation would fail during tests.
``_allow_unsafe_search_url`` relaxes the guard per test via monkeypatch, which
restores the previous environment automatically — unlike module-level
``os.environ`` assignments that leak across test modules and silently disable
the guard for the whole pytest process.
"""

import pytest


@pytest.fixture(autouse=True)
def _allow_unsafe_search_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_SERVICE_ALLOW_UNSAFE_URL", "1")
