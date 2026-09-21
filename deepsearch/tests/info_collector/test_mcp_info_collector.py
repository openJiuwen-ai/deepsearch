# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import (
    InfoRetrievalNode,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import mcp_tool_context


def _make_session():
    session = MagicMock()
    session.get_global_state.side_effect = lambda key: {
        "collector_context.section_idx": 0,
        "collector_context.step_title": "test",
        "config.web_search_engine_config": None,
        "config.local_search_engine_config": None,
        "config.info_collector_search_method": "web",
        "config.api_tools_config": {},
        "collector_context.search_queries": [],
        "collector_context.max_tool_call_turns_per_query": 2,
        "collector_context.plan_idx": 0,
        "collector_context.step_idx": 0,
        "collector_context.research_loop_count": 0,
        "collector_context.max_research_loops": 2,
        "collector_context.research_intent": {},
        "collector_context.evidence_ledger": {},
    }.get(key)
    return session


def test_pre_handle_reads_mcp_tools_from_contextvar():
    node = InfoRetrievalNode()
    mock_bundle = MagicMock()
    mock_bundle.get_tools_by_type.return_value = [
        SimpleNamespace(card=SimpleNamespace(name="mcp__srv__tool")),
    ]
    token = mcp_tool_context.set(mock_bundle)
    try:
        session = _make_session()
        with patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.adapt_llm_model_name",
            return_value="test-model",
        ), patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.llm_context"
        ) as mock_llm_ctx:
            mock_llm_ctx.get.return_value = {"test-model": MagicMock()}
            state = node._pre_handle(inputs={}, session=session, context=MagicMock())
        assert "mcp_tools" in state
        assert len(state["mcp_tools"]) == 1
    finally:
        mcp_tool_context.reset(token)


def test_pre_handle_returns_empty_mcp_tools_when_no_bundle():
    node = InfoRetrievalNode()
    token = mcp_tool_context.set(None)
    try:
        session = _make_session()
        with patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.adapt_llm_model_name",
            return_value="test-model",
        ), patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.llm_context"
        ) as mock_llm_ctx:
            mock_llm_ctx.get.return_value = {"test-model": MagicMock()}
            state = node._pre_handle(inputs={}, session=session, context=MagicMock())
        assert state["mcp_tools"] == []
    finally:
        mcp_tool_context.reset(token)


@pytest.mark.asyncio
async def test_prepare_collector_tool_is_async_and_includes_mcp():
    node = InfoRetrievalNode()
    mock_mcp_tool = MagicMock()
    mock_mcp_tool.card.name = "mcp__srv__tool"
    mock_mcp_tool.card.tool_info.return_value = {"name": "mcp__srv__tool"}
    state = {
        "search_method": "web",
        "api_tools_config": {},
        "mcp_tools": [mock_mcp_tool],
    }
    tool_list, tool_dict = await node._prepare_collector_tool(state)
    assert "mcp__srv__tool" in tool_dict
