# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    SubReporterNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ChapterSidecar,
    SubReportContent,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId


def test_chapter_sidecar_defaults_and_sub_report_serialization():
    sidecar = ChapterSidecar(chapter_summary="章节摘要")
    content = SubReportContent(sub_report_chapter_sidecar=sidecar)

    restored = SubReportContent.model_validate(content.model_dump())

    assert sidecar.key_findings == []
    assert sidecar.risk_points == []
    assert restored.sub_report_chapter_sidecar == sidecar


@pytest.mark.asyncio
@patch(
    "openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats",
    new_callable=AsyncMock,
)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_sub_report_sidecar_repairs_json_and_normalizes_soft_fields(
    mock_llm_context,
    mock_ainvoke_llm,
):
    mock_ainvoke_llm.return_value = {
        "content": (
            "```json\n"
            '{"chapter_summary":"摘要","key_findings":["发现",123],'
            '"risk_points":"not-a-list",}\n'
            "```"
        )
    }
    reporter = Reporter("basic")

    result = await reporter._generate_sub_report_sidecar(
        {
            "section_idx": 2,
            "language": "zh-CN",
            "report_task": "任务",
            "sub_report_content": "正文主体",
            "max_generate_retry_num": 2,
        }
    )

    assert result["warning"] == ""
    assert result["summary"] == "摘要"
    assert result["sidecar"] == ChapterSidecar(
        chapter_summary="摘要",
        key_findings=["发现"],
        risk_points=[],
    )
    assert mock_ainvoke_llm.await_args.kwargs["agent_name"] == AgentLlmName.SUB_REPORTER_SIDECAR.value


@pytest.mark.asyncio
@patch(
    "openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats",
    new_callable=AsyncMock,
)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_sub_report_sidecar_falls_back_to_full_body_after_retries(
    mock_llm_context,
    mock_ainvoke_llm,
):
    mock_ainvoke_llm.return_value = {"content": '{"key_findings":[]}'}
    reporter = Reporter("basic")
    body = "完整正文主体，不应截断。" * 400

    result = await reporter._generate_sub_report_sidecar(
        {
            "section_idx": 3,
            "language": "zh-CN",
            "report_task": "任务",
            "sub_report_content": body,
            "max_generate_retry_num": 2,
        }
    )

    assert result["sidecar"] is None
    assert result["summary"] == body
    assert "section 3" in result["warning"]
    assert "full pre-reference chapter body" in result["warning"]
    assert mock_ainvoke_llm.await_count == 2


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
@patch(
    "openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt",
    side_effect=ValueError("prompt render failed"),
)
async def test_generate_sub_report_sidecar_falls_back_when_prompt_render_fails(
    mock_apply_system_prompt,
    mock_llm_context,
):
    reporter = Reporter("basic")

    result = await reporter._generate_sub_report_sidecar(
        {
            "section_idx": 4,
            "sub_report_content": "complete body",
            "max_generate_retry_num": 2,
        }
    )

    assert result["sidecar"] is None
    assert result["summary"] == "complete body"
    assert "prompt render failed" in result["warning"]


@patch(
    "openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes."
    "add_debug_log_wrapper"
)
def test_sub_reporter_node_stores_sidecar_and_records_downgrade_warning(mock_debug_log):
    sidecar = ChapterSidecar(chapter_summary="摘要", key_findings=["发现"])
    state = {
        "section_context.warning_infos": [],
        "section_context.exception_infos": [],
    }
    session = MagicMock()
    session.get_global_state.side_effect = state.get

    def update_global_state(values):
        state.update(values)

    session.update_global_state.side_effect = update_global_state
    node = SubReporterNode()
    node.log_prefix = "section_idx: 1 | [SubReporterNode] "

    result = node._post_handle(
        {},
        {
            "success": True,
            "msg": "success",
            "section_idx": 1,
            "sub_report_content": "正文",
            "sub_report_summary": "摘要",
            "sub_report_chapter_sidecar": sidecar,
            "sub_report_sidecar_warning": "sidecar downgrade warning",
            "classified_content": [],
            "passages": [],
        },
        session,
        MagicMock(),
    )

    stored = state["section_context.sub_report_content"]
    assert result == {"next_node": NodeId.SUB_SOURCE_TRACER.value}
    assert stored.sub_report_chapter_sidecar == sidecar
    assert state["section_context.warning_infos"] == ["sidecar downgrade warning"]
    assert state["section_context.exception_infos"] == []
