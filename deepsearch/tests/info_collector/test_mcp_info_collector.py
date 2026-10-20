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


@pytest.mark.asyncio
async def test_do_invoke_propagates_mcp_tools_into_substate():
    """_do_invoke 构造的 sub_state 必须把 mcp_tools 下传到 _run_retrieval_query。

    回归问题1：sub_state 未抄 mcp_tools，_collector_main/_prepare_collector_tool
    永远读到 []，采集模型看不到 mcp{server}__{tool}。
    """
    from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery

    node = InfoRetrievalNode()
    captured = {}

    async def fake_run_retrieval_query(self, sub_state, query):
        captured["mcp_tools"] = sub_state.get("mcp_tools", [])
        return {}

    def fake_post_handle(self, inputs, algorithm_output, session, context):
        return algorithm_output

    monkeypatch_targets = [
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.InfoRetrievalNode._run_retrieval_query",
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.InfoRetrievalNode._post_handle",
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.filter_confirmed_target_locators",
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.adapt_llm_model_name",
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector.llm_context",
    ]

    fake_tool = SimpleNamespace(card=SimpleNamespace(name="mcp__srv__t"))
    mock_bundle = MagicMock()
    mock_bundle.get_tools_by_type.return_value = [fake_tool]
    token = mcp_tool_context.set(mock_bundle)
    try:
        with patch(monkeypatch_targets[0], fake_run_retrieval_query), \
             patch(monkeypatch_targets[1], fake_post_handle), \
             patch(monkeypatch_targets[2], return_value=[RetrievalQuery(query="test")]), \
             patch(monkeypatch_targets[3], return_value="test-model"), \
             patch(monkeypatch_targets[4]) as mock_llm_ctx:
            mock_llm_ctx.get.return_value = {"test-model": MagicMock()}
            session = _make_session()
            await node._do_invoke(inputs={}, session=session, context=MagicMock())
    finally:
        mcp_tool_context.reset(token)

    assert captured.get("mcp_tools"), "sub_state must propagate mcp_tools to _run_retrieval_query"
    assert captured["mcp_tools"][0].card.name == "mcp__srv__t"
