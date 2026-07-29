from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service import (
    CollectorExecutionService,
    CollectorInputBuildConfig,
    CollectorInputBuildParams,
    CollectorRunPlanConfig,
    run_info_collector_sub_graph,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Message,
    Plan,
    RetrievalQuery,
    Step,
    StepType,
)


@pytest.mark.asyncio
async def test_run_plan_updates_collecting_steps_and_returns_aggregate_result():
    plan = Plan(
        id="1",
        title="主题",
        thought="思路",
        is_research_completed=False,
        steps=[
            Step(
                type=StepType.INFO_COLLECTING,
                title="步骤1",
                description="收集资料",
            )
        ],
    )
    mock_run = AsyncMock(
        return_value={
            "info_summary": "摘要",
            "evaluation": "足够",
            "history_queries": [RetrievalQuery(query="q1")],
            "doc_infos": [{"title": "doc", "url": "u"}],
            "source_store": {"web_1": "正文"},
        }
    )
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service."
        "run_info_collector_sub_graph",
        mock_run,
    ):
        service = CollectorExecutionService()
        result = await service.run_plan(
            plan=plan,
            run_config=CollectorRunPlanConfig(
                language="zh-CN",
                section_idx=1,
                max_search_query_count=2,
                max_research_loops=2,
                max_tool_call_turns_per_query=4,
            ),
            session=MagicMock(),
            context=MagicMock(),
        )

    assert result.info_summary == "摘要"
    assert result.evaluation == "足够"
    assert result.collect_steps[0].retrieval_queries == [RetrievalQuery(query="q1")]
    assert result.collected_doc_num == 1
    assert result.doc_infos == [{"title": "doc", "url": "u"}]
    assert result.source_store == {"web_1": "正文"}
    assert result.messages == [Message(role="assistant", content="摘要")]
    assert result.collect_steps[0].step_result == "摘要"


def test_input_build_accepts_named_config_object():
    plan = Plan(
        id="1",
        title="主题",
        thought="思路",
        is_research_completed=False,
        steps=[],
    )
    step = Step(type=StepType.INFO_COLLECTING, title="步骤1", description="收集资料")

    agent_input = CollectorExecutionService._input_build(
        CollectorInputBuildParams(
            plan=plan,
            step=step,
            language="zh-CN",
            section_idx=1,
            build_config=CollectorInputBuildConfig(
                max_search_query_count=2,
                max_research_loops=3,
                max_tool_call_turns_per_query=4,
            ),
        )
    )

    assert agent_input["language"] == "zh-CN"
    assert agent_input["section_idx"] == 1
    assert agent_input["plan_idx"] == "1"
    assert agent_input["step_title"] == "步骤1"
    assert agent_input["max_search_query_count"] == 2
    assert "initial_search_query_count" not in agent_input
    assert agent_input["max_research_loops"] == 3
    assert agent_input["max_tool_call_turns_per_query"] == 4
    assert "max_react_recursion_limit" not in agent_input


def test_input_build_includes_research_intent():
    plan = Plan(
        id="1",
        title="主题",
        thought="思路",
        is_research_completed=False,
        steps=[],
    )
    step = Step(type=StepType.INFO_COLLECTING, title="步骤1", description="收集资料")

    agent_input = CollectorExecutionService._input_build(
        CollectorInputBuildParams(
            plan=plan,
            step=step,
            language="zh-CN",
            section_idx=1,
            build_config=CollectorInputBuildConfig(
                max_search_query_count=2,
                max_research_loops=3,
                max_tool_call_turns_per_query=4,
            ),
            research_intent={"exclude_domains": ["csdn.net"]},
        )
    )

    assert agent_input["research_intent"] == {"exclude_domains": ["csdn.net"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("collector_payload", "expect_info_summary", "expect_step_result", "expect_evaluation"),
    [
        (
            {"history_queries": [], "doc_infos": [], "evaluation": "未产出摘要"},
            None,
            None,
            "未产出摘要",
        ),
        (
            {"info_summary": "", "evaluation": "不足", "history_queries": [], "doc_infos": []},
            "",
            "",
            "不足",
        ),
    ],
    ids=["info_summary_key_missing", "info_summary_empty_string"],
)
async def test_run_plan_handles_absent_or_empty_info_summary(
    collector_payload,
    expect_info_summary,
    expect_step_result,
    expect_evaluation,
):
    plan = Plan(
        id="1",
        title="主题",
        thought="思路",
        is_research_completed=False,
        steps=[
            Step(type=StepType.INFO_COLLECTING, title="步骤1", description="收集资料"),
        ],
    )
    mock_run = AsyncMock(return_value=collector_payload)
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service."
        "run_info_collector_sub_graph",
        mock_run,
    ):
        service = CollectorExecutionService()
        result = await service.run_plan(
            plan=plan,
            run_config=CollectorRunPlanConfig(
                language="zh-CN",
                section_idx=1,
                max_search_query_count=2,
                max_research_loops=2,
                max_tool_call_turns_per_query=4,
            ),
            session=MagicMock(),
            context=MagicMock(),
        )

    assert result.info_summary == expect_info_summary
    assert result.collect_steps[0].step_result == expect_step_result
    assert result.evaluation == expect_evaluation
    assert result.messages == [Message(role="assistant", content="")]
    assert result.doc_infos == []


@pytest.mark.asyncio
async def test_run_info_collector_sub_graph_uses_isolated_workflow_session_for_wrapped_sessions():
    agent_input = {"messages": [{"role": "user", "content": "task"}], "language": "zh-CN"}
    context = MagicMock()

    inner_session = MagicMock()
    inner_session.callback_manager.return_value = MagicMock()
    inner_session.stream_writer_manager.return_value = None

    outer_session = MagicMock()
    outer_session._inner = inner_session
    outer_session.get_global_state.side_effect = lambda key: {
        "config": {"mock": True},
        "collector_context": {},
    }.get(key)

    compiled_graph = AsyncMock()

    async def _fake_invoke(inputs, workflow_session):
        workflow_session.state().update_global(
            {"collector_context": {"info_summary": "摘要", "doc_infos": [{"title": "doc"}]}}
        )
        workflow_session.state().commit()

    compiled_graph.invoke.side_effect = _fake_invoke

    collector_internal = MagicMock()
    collector_internal.compile.return_value = compiled_graph
    collector_internal.config.return_value = MagicMock()
    collector_internal.reset = AsyncMock()

    collector_graph = MagicMock()
    collector_graph.card.id = "collector_graph"
    collector_graph._internal = collector_internal
    collector_graph.invoke = AsyncMock()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
        "collector_execution_service.build_info_collector_sub_graph",
        return_value=collector_graph,
    ):
        result = await run_info_collector_sub_graph(agent_input, outer_session, context)

    assert result == {"info_summary": "摘要", "doc_infos": [{"title": "doc"}]}
    collector_internal.compile.assert_called_once()
    compiled_graph.invoke.assert_awaited_once()
