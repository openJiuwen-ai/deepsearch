"""Tests for _collect_step_summaries in editor_team_nodes."""

from types import SimpleNamespace

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    _collect_step_summaries,
)


def test_collect_step_summaries_with_simple_namespace_objects():
    plans = [
        SimpleNamespace(
            steps=[
                SimpleNamespace(
                    description="研究出口数据",
                    step_result="2024年出口120万辆",
                    evaluation="数据已充分覆盖",
                    title="Step A",
                ),
                SimpleNamespace(
                    description="分析目的国",
                    step_result="欧洲占40%",
                    evaluation="部分覆盖，缺少东南亚数据",
                    title="Step B",
                ),
            ]
        )
    ]

    result = _collect_step_summaries(plans)

    assert len(result) == 2
    assert result[0]["plan_idx"] == 0
    assert result[0]["step_idx"] == 0
    assert result[0]["title"] == "Step A"
    assert result[0]["description"] == "研究出口数据"
    assert result[0]["step_result"] == "2024年出口120万辆"
    assert result[0]["evaluation"] == "数据已充分覆盖"

    assert result[1]["plan_idx"] == 0
    assert result[1]["step_idx"] == 1
    assert result[1]["title"] == "Step B"


def test_collect_step_summaries_with_dict_objects():
    plans = [
        {
            "steps": [
                {
                    "description": "搜索政策",
                    "step_result": "找到了补贴政策",
                    "evaluation": "充分",
                    "title": "Policy Step",
                }
            ]
        }
    ]

    result = _collect_step_summaries(plans)

    assert len(result) == 1
    assert result[0]["plan_idx"] == 0
    assert result[0]["step_idx"] == 0
    assert result[0]["description"] == "搜索政策"
    assert result[0]["step_result"] == "找到了补贴政策"


def test_collect_step_summaries_multiple_plans():
    plans = [
        SimpleNamespace(
            steps=[
                SimpleNamespace(description="step1", step_result="r1", evaluation="e1", title="t1"),
            ]
        ),
        SimpleNamespace(
            steps=[
                SimpleNamespace(description="step2", step_result="r2", evaluation="e2", title="t2"),
                SimpleNamespace(description="step3", step_result="r3", evaluation="e3", title="t3"),
            ]
        ),
    ]

    result = _collect_step_summaries(plans)

    assert len(result) == 3
    assert result[0]["plan_idx"] == 0
    assert result[1]["plan_idx"] == 1
    assert result[1]["step_idx"] == 0
    assert result[2]["step_idx"] == 1


def test_collect_step_summaries_missing_fields_use_empty_string():
    # Use dict instead of empty SimpleNamespace (which lacks .get())
    # Steps with no step_result AND no evaluation are filtered out
    plans = [
        {"steps": [{"step_result": "some data"}]}
    ]

    result = _collect_step_summaries(plans)

    assert len(result) == 1
    assert result[0]["title"] == ""
    assert result[0]["description"] == ""
    assert result[0]["step_result"] == "some data"
    assert result[0]["evaluation"] == ""


def test_collect_step_summaries_filters_empty_steps():
    """Steps with no step_result AND no evaluation should be filtered out."""
    plans = [
        {"steps": [
            {"step_result": "data collected", "evaluation": "sufficient", "description": "step A"},
            {},  # empty step → filtered
            {"description": "step C"},  # no result/evaluation → filtered
            {"step_result": None, "evaluation": None},  # both None → filtered
            {"step_result": "", "evaluation": ""},  # both empty → filtered
        ]}
    ]
    result = _collect_step_summaries(plans)
    assert len(result) == 1
    assert result[0]["description"] == "step A"


def test_collect_step_summaries_none_fields_become_empty_string():
    # Steps with None step_result AND None evaluation are filtered out
    # Include at least one step with data to verify None fields become ""
    plans = [
        SimpleNamespace(
            steps=[
                SimpleNamespace(
                    description="test desc",
                    step_result=None,
                    evaluation="sufficient",
                    title=None,
                ),
            ]
        )
    ]

    result = _collect_step_summaries(plans)

    assert len(result) == 1
    assert result[0]["title"] == ""
    assert result[0]["description"] == "test desc"
    assert result[0]["step_result"] == ""
    assert result[0]["evaluation"] == "sufficient"


def test_collect_step_summaries_empty_plans():
    assert _collect_step_summaries([]) == []


def test_collect_step_summaries_none_plans():
    assert _collect_step_summaries(None) == []


def test_collect_step_summaries_plan_with_no_steps():
    plans = [SimpleNamespace(steps=[])]

    result = _collect_step_summaries(plans)

    assert result == []


def test_collect_step_summaries_dict_plan_with_empty_steps():
    plans = [{"steps": []}]

    assert _collect_step_summaries(plans) == []


def test_collect_step_summaries_mixed_simple_namespace_and_dict():
    plans = [
        SimpleNamespace(
            steps=[
                SimpleNamespace(description="ns-step", step_result="ns-result", evaluation="ns-eval", title="ns-title"),
                {
                    "description": "dict-step",
                    "step_result": "dict-result",
                    "evaluation": "dict-eval",
                    "title": "dict-title",
                },
            ]
        )
    ]

    result = _collect_step_summaries(plans)

    assert len(result) == 2
    assert result[0]["description"] == "ns-step"
    assert result[1]["description"] == "dict-step"
    assert result[1]["step_result"] == "dict-result"
