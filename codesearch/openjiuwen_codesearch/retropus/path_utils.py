# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared path helpers for Retropus KG / retrieval tooling."""

from __future__ import annotations

import re

# Union of patterns formerly duplicated in graph_tools + inherits (keep in sync).
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|"
    r"(^|/)test_[^/]+\.(py|js|ts|tsx|java|go|rb|rs|c|cc|cpp|cxx)$|"
    r"(^|/)[^/]+_test\.(py|js|ts|tsx|java|go|rb|rs|c|cc|cpp|cxx)$|"
    r"(^|/)conftest\.py$",
    re.IGNORECASE,
)


def is_test_path(rel: str) -> bool:
    """True if a repo-relative path looks like a test file or test directory."""
    return bool(_TEST_PATH_RE.search(rel.replace("\\", "/")))
