# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Import-surface checks against the installed openjiuwen package."""

import importlib

import pytest

pytestmark = pytest.mark.system

_SYMBOLS = [
    ("openjiuwen.core.context_engine.base", "ModelContext"),
    ("openjiuwen.core.graph.executable", "Input"),
    ("openjiuwen.core.graph.executable", "Output"),
    ("openjiuwen.core.session.node", "Session"),
    ("openjiuwen.core.workflow", "WorkflowComponent"),
    ("openjiuwen.core.workflow.components.flow.branch_router", "BranchRouter"),
    ("openjiuwen.core.foundation.llm", "Model"),
    ("openjiuwen.core.foundation.llm", "ModelClientConfig"),
    ("openjiuwen.core.foundation.llm", "ModelRequestConfig"),
    ("openjiuwen.core.foundation.llm", "UserMessage"),
    ("openjiuwen.core.foundation.llm", "ToolMessage"),
]


@pytest.mark.parametrize("module_name, symbol", _SYMBOLS)
def test_base_openjiuwen_import_exists(openjiuwen_pkg, module_name, symbol):
    module = importlib.import_module(module_name)
    assert getattr(module, symbol, None) is not None, f"{module_name}.{symbol}"
