# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System-test bootstrap: package root on sys.path, sibling .env."""

import sys
from pathlib import Path

from dotenv import load_dotenv
import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(PKG_ROOT) not in sys.path:
    sys.path.append(str(PKG_ROOT))

load_dotenv(Path(__file__).with_name(".env"))


@pytest.fixture(scope="session")
def openjiuwen_pkg():
    return pytest.importorskip("openjiuwen", reason="system tests need extras: workflow")
