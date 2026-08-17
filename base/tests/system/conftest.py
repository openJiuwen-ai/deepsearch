# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System-test bootstrap: package root on sys.path, sibling .env."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(PKG_ROOT) not in sys.path:
    sys.path.append(str(PKG_ROOT))

ENV_PATH = Path(__file__).with_name(".env")


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_env(ENV_PATH)


@pytest.fixture(scope="session")
def openjiuwen_pkg():
    return pytest.importorskip("openjiuwen", reason="system tests need extras: workflow")
