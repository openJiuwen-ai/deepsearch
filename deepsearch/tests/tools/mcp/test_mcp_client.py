# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client import McpClient

@pytest.mark.asyncio
async def test_mcp_client_connect_initializes_session():
    client = McpClient({
        "server_url": "https://mcp.example.com/sse",
        "transport_type": "sse",
        "headers": None,
        "timeout": 30.0,
    })
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.sse_client"
    ) as mock_sse, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.ClientSession"
    ) as mock_session_cls:
        mock_transport_cm = AsyncMock()
        mock_transport_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_transport_cm.__aexit__ = AsyncMock(return_value=None)
        mock_sse.return_value = mock_transport_cm

        mock_session_cm = AsyncMock()
        mock_session_cls.return_value = mock_session_cm
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session_cls.return_value)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        await client.connect()
        assert client._session is not None


@pytest.mark.asyncio
async def test_mcp_client_close_clears_session():
    client = McpClient({"server_url": "https://mcp.example.com/sse", "transport_type": "sse"})
    client._session = MagicMock()
    client._cm_stack = MagicMock()
    client._cm_stack.aclose = AsyncMock()
    await client.close()
    assert client._session is None
    assert client._cm_stack is None


@pytest.mark.asyncio
async def test_mcp_client_list_tools_raises_when_not_connected():
    client = McpClient({"server_url": "https://mcp.example.com/sse", "transport_type": "sse"})
    with pytest.raises(RuntimeError, match="not connected"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_mcp_client_call_tool_raises_when_not_connected():
    client = McpClient({"server_url": "https://mcp.example.com/sse", "transport_type": "sse"})
    with pytest.raises(RuntimeError, match="not connected"):
        await client.call_tool("test", {})


@pytest.mark.asyncio
async def test_streamable_http_connect_passes_headers_and_timeout_to_httpx():
    """默认 streamable_http 配置下,headers 与 timeout 必须经 httpx.AsyncClient 注入。

    回归:
    - 问题2/4/5 — streamable_http_client 不接收 headers/timeout,
      必须经 http_client=httpx.AsyncClient(...) 传入,否则鉴权头丢失、超时失效。
    """
    captured = {}

    class _FakeTransport:
        def __init__(self, url, *, http_client=None, terminate_on_close=True):
            captured["url"] = url
            captured["headers"] = dict(http_client.headers) if http_client else {}
            captured["timeout"] = getattr(http_client, "timeout", None)
            self._http_client = http_client

        async def __aenter__(self):
            return MagicMock(), MagicMock(), lambda: None

        async def __aexit__(self, *exc):
            if self._http_client is not None:
                await self._http_client.aclose()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.streamable_http_client",
        side_effect=_FakeTransport,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.ClientSession"
    ) as mock_session_cls:
        mock_session_cm = AsyncMock()
        mock_session_instance = MagicMock()
        mock_session_instance.initialize = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_cm

        client = McpClient({
            "server_url": "https://mcp.example.com/mcp",
            "transport_type": "streamable_http",
            "headers": {"Authorization": "Bearer secret-token"},
            "timeout": 5.0,
        })
        await client.connect()

    assert captured["url"] == "https://mcp.example.com/mcp"
    assert captured["headers"].get("authorization") == "Bearer secret-token"
    assert captured["timeout"] is not None


@pytest.mark.asyncio
async def test_connect_cleans_up_stack_when_initialize_raises():
    """connect 在 initialize 抛异常时必须 aclose 局部 stack,避免泄漏 transport 资源。

    回归:
    - 局部 stack 仅在 initialize 成功后才赋给 self._cm_stack;initialize 抛异常时
      AsyncExitStack 不会自动清理已进入的 transport 上下文,导致后台连接/读循环泄漏,
      且 bundle 的 client.close() 因 _cm_stack is None 直接跳过 aclose,兜底无效。
    """
    aexit_calls = []

    class _TrackingTransport:
        async def __aenter__(self):
            return MagicMock(), MagicMock(), lambda: None

        async def __aexit__(self, *exc):
            aexit_calls.append(exc)
            return False

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.streamable_http_client",
        return_value=_TrackingTransport(),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.ClientSession"
    ) as mock_session_cls:
        mock_session_cm = AsyncMock()
        mock_session_instance = MagicMock()
        mock_session_instance.initialize = AsyncMock(side_effect=RuntimeError("init failed"))
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_cm

        client = McpClient({
            "server_url": "https://mcp.example.com/mcp",
            "transport_type": "streamable_http",
            "headers": None,
            "timeout": 30.0,
        })

        with pytest.raises(RuntimeError, match="init failed"):
            await client.connect()

    # transport __aexit__ 必须被调用(局部 stack 已 aclose,资源清理)
    assert len(aexit_calls) == 1
    # 未赋值给 self,后续 close 不会重复 aclose
    assert client._cm_stack is None
    assert client._session is None


@pytest.mark.asyncio
async def test_streamable_http_close_closes_external_http_client():
    """close() 必须显式关闭自建 httpx.AsyncClient,避免连接/FD 泄漏。

    回归:
    - mcp SDK (streamable_http.py:637-654) 在外部传入 http_client 时不管理其生命周期
      (# Only manage client lifecycle if we created it)
    - McpClient 自建 httpx.AsyncClient 注入鉴权头/超时后传入 SDK,close() 只 aclose
      _cm_stack 不会触及 http_client,长驻服务下持续泄漏连接池与 FD。
    """
    closed = {"count": 0}

    class _SdkLikeTransport:
        """模拟 mcp SDK 行为:不关闭外部传入的 http_client。"""

        def __init__(self, url, *, http_client=None, terminate_on_close=True):
            self._http_client = http_client

        async def __aenter__(self):
            return MagicMock(), MagicMock(), lambda: None

        async def __aexit__(self, *exc):
            # SDK 契约:外部 client 由调用方管理,这里不关闭
            return False

    class _FakeHttpxClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def aclose(self):
            closed["count"] += 1

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.streamable_http_client",
        side_effect=_SdkLikeTransport,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.ClientSession"
    ) as mock_session_cls, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.httpx.AsyncClient",
        side_effect=_FakeHttpxClient,
    ):
        mock_session_cm = AsyncMock()
        mock_session_instance = MagicMock()
        mock_session_instance.initialize = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_cm

        client = McpClient({
            "server_url": "https://mcp.example.com/mcp",
            "transport_type": "streamable_http",
            "headers": {"Authorization": "Bearer x"},
            "timeout": 5.0,
        })
        await client.connect()
        await client.close()

    assert closed["count"] == 1, "自建 httpx.AsyncClient 必须在 close() 时被关闭一次"


@pytest.mark.asyncio
async def test_streamable_http_connect_failure_closes_external_http_client():
    """connect 异常路径必须也关闭自建 httpx.AsyncClient,避免泄漏。

    回归:
    - connect 在 initialize 抛异常时局部 stack.aclose() 不触及 http_client,
      既泄漏资源,又让后续 close() 因 _http_client 未记录而无从关闭。
    """

    class _SdkLikeTransport:
        def __init__(self, url, *, http_client=None, terminate_on_close=True):
            pass

        async def __aenter__(self):
            return MagicMock(), MagicMock(), lambda: None

        async def __aexit__(self, *exc):
            return False

    closed = {"count": 0}

    class _FakeHttpxClient:
        def __init__(self, **kwargs):
            pass

        async def aclose(self):
            closed["count"] += 1

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.streamable_http_client",
        side_effect=_SdkLikeTransport,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.ClientSession"
    ) as mock_session_cls, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.httpx.AsyncClient",
        side_effect=_FakeHttpxClient,
    ):
        mock_session_cm = AsyncMock()
        mock_session_instance = MagicMock()
        mock_session_instance.initialize = AsyncMock(side_effect=RuntimeError("init failed"))
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_cm

        client = McpClient({
            "server_url": "https://mcp.example.com/mcp",
            "transport_type": "streamable_http",
        })
        with pytest.raises(RuntimeError, match="init failed"):
            await client.connect()

    assert closed["count"] == 1, "异常路径也必须关闭自建 httpx.AsyncClient"
