# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeSearch 默认工具注册表（process-global lazy singleton）。

阶段逻辑（reasoning / tool / finalize）在 ``CodeSearchAgent`` 上；
图节点与 react 引擎共用该类方法。
"""

from typing import Optional

from openjiuwen_codesearch.algorithm.search_tools.registry import (
    ToolSpec,
    build_default_registry,
)

_REGISTRY: Optional[dict[str, ToolSpec]] = None


def get_registry() -> dict[str, ToolSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    return _REGISTRY
