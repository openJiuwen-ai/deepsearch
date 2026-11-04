# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""coverage_rule_block_enable 开关全链路传递测试。

PR !406 评审修复回归：开关必须从请求 agent_config 经 StartNode 白名单、
会话 config、SubReporterNode._pre_handle 一路下发到 evidence._prepare_evidence
的消费端；任何一环断链都会导致子报告永远按默认开启执行。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.algorithm.report import evidence as evidence_module
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import StartNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    SubReporterNode,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    llm_context,
    session_context,
)


def test_start_node_passes_coverage_rule_block_enable_to_session_config():
    """StartNode.invoke 白名单必须复制 coverage_rule_block_enable 到会话 config。"""
    for requested, expected in [(False, False), (True, True)]:
        node = StartNode()
        session = Mock()
        session.update_global_state = Mock()
        context = Mock()
        inputs = {
            "agent_config": {
                "llm_config": {},
                "web_search_engine_config": {"search_engine_name": "tavily"},
                "local_search_engine_config": {"search_engine_name": "openapi"},
                "coverage_rule_block_enable": requested,
            },
            "thread_id": "thread-cov",
            "interrupt_feedback": "",
        }

        asyncio.run(node.invoke(inputs, session, context))

        merged_config = session.update_global_state.call_args.args[0]["config"]
        assert merged_config["coverage_rule_block_enable"] is expected


@pytest.mark.parametrize(
    ("session_value", "expected"),
    [
        (True, True),
        (False, False),
        # 缺省语义：请求不带 agent_config 时会话无此键（get_global_state 返回
        # None），节点必须归一回默认开启；直接透传会让消费端 .get(key, True)
        # 命中"键存在值 None"而误关闭。
        (None, True),
    ],
)
def test_sub_reporter_node_pre_handle_dispatches_coverage_rule_block_enable(
    session_value, expected
):
    """经真实 SubReporterNode._pre_handle 预处理后，开关三态正确下发。"""
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
        "config.coverage_rule_block_enable": session_value,
        "config.llm_config": {"general": {"model_name": "mock_model"}},
    }
    session.get_global_state.side_effect = state_map.get

    current_inputs = node._pre_handle({}, session, context)

    assert current_inputs["coverage_rule_block_enable"] is expected


def _stub_selection_methods(reporter):
    """打桩 LLM 依赖的选材环节，只有规则覆盖块组装保留真身。"""
    reporter._generate_section_rationales = AsyncMock(return_value=(
        [{"id": "R1", "description": "分析要点"}], ""
    ))
    reporter._extract_and_score_documents = AsyncMock(return_value=(
        {
            "filtered_passages": [{
                "doc_url": "https://example.com/doc-1",
                "doc_title": "Doc 1",
                "passage_text": "选中段落",
            }],
            "coverage_matrix": {"passage_0": {"R1": 0.9}},
        }, ""
    ))
    selected_passage = {
        "doc_url": "https://example.com/doc-1",
        "doc_title": "Doc 1",
        "passage_text": "选中段落",
    }
    reporter._select_by_rationale_coverage = MagicMock(
        return_value=([selected_passage], [])
    )
    reporter._write_doc_selection_debug = MagicMock()
    return {
        "classified_content": [{"index": 1, "is_fulltext": True}],
        "sub_section_core_content": ["Document 1 key passages:\n- k"],
        "sub_section_references": ["[1] Doc 1"],
        "structured_evidence_guide": "",
        "fulltext_count": 1,
        "remaining_count": 0,
        "remaining_passages": [],
        "remaining_passage_keys": [],
    }


def _fulltext_evidence_with_facts():
    """携带基准区外事实句的全文证据（>500 字符前导叙述进基准区不回灌）。

    属性集对齐 _prepare_evidence 尾部 debug 字典读取口径。
    """
    lead = "本节为背景叙述。" * 100
    return [
        SimpleNamespace(
            citation_index=1,
            url="https://example.com/doc-1",
            doc_title="Doc 1",
            doc_time="",
            original_content=(
                lead + "2025年公司营收100亿元，同比增长20%。该产品定价99美元/月。"
            ),
            key_passages=[],
            coverage_scores={},
            fetch_success=True,
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatch_value", "rule_block_expected"),
    [
        (True, True),
        (False, False),
        ("missing", True),
    ],
)
async def test_prepare_evidence_consumes_dispatched_coverage_switch(
    dispatch_value, rule_block_expected
):
    """端到端：SubReporterNode 下发值进入真实 _prepare_evidence 的消费端。

    关闭时断言规则抽取入口 _append_rule_coverage_to_core 不被调用、
    大纲证据不含 COVERAGE PASSAGES 块；开启（含缺省兜底）时被调用。
    """
    evidences = _fulltext_evidence_with_facts()
    real_append = evidence_module._append_rule_coverage_to_core

    mock_session = MagicMock()
    mock_session.write_custom_stream = AsyncMock()
    session_token = session_context.set(mock_session)
    llm_token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        fulltext_result = _stub_selection_methods(reporter)
        fulltext_result["fulltext_evidences"] = evidences
        reporter._generate_sub_section_outline = AsyncMock(return_value={
            "rs_success": True,
            "sub_section_outline": "# 1 章节",
        })
        reporter.check_chapter_format = MagicMock(return_value=(True, ""))
        reporter._write_subsection_reports = AsyncMock(return_value={
            "success": True,
            "result": "# 1 章节\n\n正文 [citation:1]",
        })

        with (
            patch(
                "openjiuwen_deepsearch.algorithm.report.evidence.enrich_fulltext_for_section",
                return_value=fulltext_result,
            ),
            patch(
                "openjiuwen_deepsearch.algorithm.report.evidence._append_rule_coverage_to_core",
                wraps=real_append,
            ) as spy_append,
        ):
            current_inputs = {
                "language": "zh-CN",
                "section_idx": 1,
                "section_task": "章节",
                "report_task": "总报告",
                "section_iscore": False,
                "passages": [{
                    "title": "Doc 1",
                    "url": "https://example.com/doc-1",
                    "original_content": "全文",
                }],
                "research_intent": {},
                "visualization_enable": False,
                "max_generate_retry_num": 1,
            }
            if dispatch_value != "missing":
                current_inputs["coverage_rule_block_enable"] = dispatch_value

            success, _, _, _ = await reporter.generate_sub_report(current_inputs)
    finally:
        llm_context.reset(llm_token)
        session_context.reset(session_token)

    assert success is True
    core_content = current_inputs["sub_section_core_content"]
    has_rule_block = any(
        block.startswith("===== COVERAGE PASSAGES =====") for block in core_content
    )
    if rule_block_expected:
        assert spy_append.call_count == 1
        assert has_rule_block
        assert any("99美元/月" in block for block in core_content)
    else:
        assert spy_append.call_count == 0
        assert not has_rule_block
