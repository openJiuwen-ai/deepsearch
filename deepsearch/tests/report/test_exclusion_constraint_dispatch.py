# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""exclusion_constraint_enable 开关全链路传递测试。

开关默认关闭（关闭时行为等同 baseline）。它必须能从请求 agent_config 经
StartNode 白名单 → 会话 config → 各节点 _pre_handle 一路下发到消费端；
任何一环断链都会让"按需开启"失效——该开的没开，或该关的开了。
"""

from unittest.mock import MagicMock

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.config.config import AgentConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import StartNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    SubReporterNode,
)
from server.schemas.deepsearch_run import DeepSearchRequest


def test_agent_config_defaults_exclusion_constraint_off():
    """AgentConfig 默认关闭禁引约束。"""
    assert AgentConfig().exclusion_constraint_enable is False


def test_run_schema_defaults_exclusion_constraint_off():
    """请求体不传该字段时默认关闭。"""
    request = DeepSearchRequest(space_id="default", conversation_id="c1", message="m")
    assert request.exclusion_constraint_enable is False


def test_start_node_passes_exclusion_constraint_enable_to_session_config():
    """StartNode.invoke 白名单必须复制 exclusion_constraint_enable 到会话 config。"""
    for requested, expected in [(True, True), (False, False)]:
        node = StartNode()
        session = MagicMock(spec=Session)
        session.update_global_state = MagicMock()
        context = MagicMock(spec=ModelContext)
        inputs = {
            "agent_config": {
                "llm_config": {},
                "web_search_engine_config": {"search_engine_name": "tavily"},
                "local_search_engine_config": {"search_engine_name": "openapi"},
                "exclusion_constraint_enable": requested,
            },
            "thread_id": "thread-excl",
            "interrupt_feedback": "",
        }

        import asyncio

        asyncio.run(node.invoke(inputs, session, context))

        merged_config = session.update_global_state.call_args.args[0]["config"]
        assert merged_config["exclusion_constraint_enable"] is expected


def test_sub_reporter_node_dispatches_exclusion_constraint_enable():
    """经真实 SubReporterNode._pre_handle 后，开关正确下发到 current_inputs。

    缺键语义：会话无此键时 get_global_state 返回 None，必须归一到 False（关闭），
    而不是消费端默认开启。
    """
    for session_value, expected in [(True, True), (False, False), (None, False)]:
        node = SubReporterNode()
        session = MagicMock(spec=Session)
        context = MagicMock(spec=ModelContext)
        state_map = {
            "section_context.section_idx": "1",
            "section_context.report_type_policy": {},
            "section_context.research_intent": {},
            "section_context.section_local_contract": {},
            "section_context.report_template": "",
            "section_context.language": "zh-CN",
            "section_context.report_format": None,
            "section_context.report_task": "总报告",
            "section_context.section_task": "章节",
            "section_context.section_iscore": False,
            "section_context.section_description": "",
            "section_context.section_format_requirements": [],
            "section_context.history_plans": [],
            "section_context.current_outline": "",
            "section_context.sub_report_background_knowledge": [],
            "config.report_style": "scholarly",
            "config.report_max_generate_retry_num": 3,
            "config.sub_report_classify_doc_infos_res_top_k_num": 15,
            "config.visualization_enable": False,
            "config.exclusion_constraint_enable": session_value,
            "config.llm_config": {"general": {"model_name": "mock_model"}},
        }
        session.get_global_state.side_effect = state_map.get

        current_inputs = node._pre_handle({}, session, context)

        assert current_inputs["exclusion_constraint_enable"] is expected
