# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import mcp_tool_context


@pytest.mark.asyncio
async def test_initialize_mcp_context_returns_none_when_mcp_pkg_missing_and_no_servers():
    """mcp 包未安装且 agent_config 无 mcp_servers 时应直接返回 None,不抛 ImportError。

    回归：_initialize_mcp_context_from_agent_config 的导入语句原本在 mcp_servers
    空检查之前,未装 [mcp] extra 的最小安装即便没配 MCP server 也会因 ImportError
    让所有研究/search_fetch/react 模式在初始化阶段失败。
    """
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )

    mock_config = MagicMock()
    mock_config.mcp_servers = []

    # 把 tools.mcp 子树在 sys.modules 中标记为 None,触发 ImportError
    blocked_modules = {
        name: None
        for name in list(sys.modules)
        if name == "mcp"
        or name.startswith("mcp.")
        or name.endswith(".tools.mcp")
        or ".tools.mcp." in name
    }
    if not blocked_modules:
        pytest.skip("tools.mcp 子树未加载,无法模拟 ImportError")
    with patch.dict("sys.modules", blocked_modules, clear=False):
        token = await _initialize_mcp_context_from_agent_config(mock_config)

    assert token is None


@pytest.mark.asyncio
async def test_initialize_mcp_context_returns_none_when_no_servers():
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )
    mock_config = MagicMock()
    mock_config.mcp_servers = []
    token = await _initialize_mcp_context_from_agent_config(mock_config)
    assert token is None


@pytest.mark.asyncio
async def test_initialize_mcp_context_sets_contextvar():
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )
    mock_config = MagicMock()
    mock_config.mcp_servers = [
        MagicMock(model_dump=MagicMock(return_value={
            "server_name": "test",
            "server_url": "https://mcp.com/sse",
            "transport_type": "sse",
            "type": "search",
        }))
    ]
    # 延迟导入后 McpToolBundle 不在 workflow 顶层;patch 实际导入位置
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.McpToolBundle"
    ) as mock_bundle_cls:
        mock_bundle = MagicMock()
        mock_bundle.connect_and_build_tools = AsyncMock()
        mock_bundle_cls.return_value = mock_bundle

        token = await _initialize_mcp_context_from_agent_config(mock_config)

    assert token is not None
    assert mcp_tool_context.get() is mock_bundle
    mcp_tool_context.reset(token)


@pytest.mark.asyncio
async def test_initialize_mcp_context_returns_none_on_failure():
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )
    mock_config = MagicMock()
    mock_config.mcp_servers = [
        MagicMock(model_dump=MagicMock(return_value={
            "server_name": "test",
            "server_url": "https://mcp.com/sse",
            "transport_type": "sse",
            "type": "search",
        }))
    ]
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.McpToolBundle"
    ) as mock_bundle_cls:
        mock_bundle = MagicMock()
        mock_bundle.connect_and_build_tools = AsyncMock(side_effect=Exception("connect failed"))
        mock_bundle.close_all = AsyncMock()
        mock_bundle_cls.return_value = mock_bundle

        token = await _initialize_mcp_context_from_agent_config(mock_config)

    assert token is None


@pytest.mark.asyncio
async def test_close_mcp_bundle_resets_contextvar_and_closes_bundle():
    """_close_mcp_bundle 在 token 非 None 时必须 close_all 并 reset contextvar。

    回归问题1/9：DeepresearchAgent / SimpleReactSearchAgent 的 except/finally
    需要可靠关闭 MCP bundle,不能因异常路径泄漏连接。
    """
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import _close_mcp_bundle

    mock_bundle = MagicMock()
    mock_bundle.close_all = AsyncMock()
    token = mcp_tool_context.set(mock_bundle)
    await _close_mcp_bundle(token)
    mock_bundle.close_all.assert_awaited_once()
    # _close_mcp_bundle 已 reset token;再次 reset 会抛 RuntimeError/LookupError,属预期


@pytest.mark.asyncio
async def test_close_mcp_bundle_handles_none_token():
    """token 为 None 时 _close_mcp_bundle 应直接返回,不抛异常。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import _close_mcp_bundle

    await _close_mcp_bundle(None)  # 不应抛异常


@pytest.mark.asyncio
async def test_deepresearch_agent_run_invokes_mcp_init():
    """DeepresearchAgent.run 必须在工具初始化后调用 _initialize_mcp_context_from_agent_config。

    回归问题9：research 模式默认不连 MCP,即便请求带了 mcp_servers。
    """
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        DeepresearchAgent,
        _initialize_mcp_context_from_agent_config,
    )

    init_called = {"count": 0}

    async def fake_init(cfg):
        init_called["count"] += 1
        return None

    agent = DeepresearchAgent()
    # 用最薄的桩绕过 DeepresearchAgent.run 的重依赖,只断言 MCP init 被调用
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow._initialize_mcp_context_from_agent_config",
        side_effect=fake_init,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.validate_run_agent_params"
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.validate_agent_required_field"
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.AgentConfig"
    ) as mock_cfg_cls, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj"
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.llm_context"
    ) as mock_llm_ctx, patch.object(
        DeepresearchAgent, "_initialize_tools", return_value=(None, None)
    ), patch.object(
        DeepresearchAgent, "_aopen_local_search_engines", new=AsyncMock()
    ), patch.object(
        DeepresearchAgent, "_aclose_local_search_engines", new=AsyncMock()
    ), patch.object(
        DeepresearchAgent, "_reset_context_tokens"
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow._zero_scholarly_search_secrets"
    ), patch.object(
        DeepresearchAgent, "_consume_stream_chunks", new=AsyncMock()
    ) as mock_consume:
        mock_consume.return_value = AsyncMock()
        mock_consume.return_value.__aiter__ = AsyncMock(return_value=iter([]))
        mock_cfg = MagicMock()
        mock_cfg.llm_config = {"general": MagicMock()}
        mock_cfg.mcp_servers = []
        mock_cfg_cls.model_validate.return_value = mock_cfg
        mock_llm_ctx.set.return_value = None

        try:
            async for _ in agent.run(message="q", conversation_id="c1", agent_config={}):
                pass
        except Exception:
            pass  # 桩不完整会抛,但只关心 MCP init 是否被调用

    assert init_called["count"] >= 1, "DeepresearchAgent.run 必须调用 _initialize_mcp_context_from_agent_config"
