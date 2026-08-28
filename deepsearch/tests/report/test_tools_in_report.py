from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    VisualizationInsertRenderContext,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section, Plan, Step, StepType
from openjiuwen_deepsearch.common.common_constants import CHINESE


def test_apply_visualization_insertions_escapes_image_title_html():
    context = VisualizationInsertRenderContext(
        report_lines=["第一段\n", "第二段\n"],
        insertions=[{"after_row": 1, "index": 1}],
        mermaid_map={1: "graph TD\nA-->B"},
        title_meta_map={
            1: {
                "image_title": '<img src=x onerror="alert(1)">',
                "citation_index": 7,
            }
        },
        newline="\n",
        language=CHINESE,
    )

    result = Reporter._apply_visualization_insertions(context)

    assert '<img src=x onerror="alert(1)">' not in result
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;[citation:7]" in result


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report_parts.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_with_llm_returns_content(mock_llm_cls, mock_ainvoke_llm):
    # 准备 mock
    # mock ainvoke_llm_with_stats 返回值
    mock_ainvoke_llm.return_value = {"content": "mocked response"}
    # mock LLMWrapper 实例
    mock_llm_instance = MagicMock()
    mock_llm_cls.return_value = mock_llm_instance

    # 初始化被测试对象
    reporter = Reporter("basic")
    reporter.gen_report_context = {}

    # 调用被测函数
    result = await reporter._generate_with_llm(
        task_type="abstract",
        prompt="report_abstract_markdown",
        content="test content"
    )

    # 断言返回值正确
    assert result == "mocked response"

    # 断言 ainvoke_llm_with_stats 被正确调用
    mock_ainvoke_llm.assert_awaited_once()
    args, kwargs = mock_ainvoke_llm.call_args
    assert kwargs["agent_name"] is not None
    assert any(msg["role"] == "user" for msg in kwargs["messages"])


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report_parts.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_with_llm_rejects_unknown_task_type(mock_llm_cls, mock_ainvoke_llm):
    mock_llm_instance = MagicMock()
    mock_llm_cls.return_value = mock_llm_instance

    reporter = Reporter("basic")
    reporter.gen_report_context = {}

    with pytest.raises(KeyError, match="Unsupported report task type"):
        await reporter._generate_with_llm(
            task_type="summary",
            prompt="report_abstract_markdown",
            content="test content"
        )

    mock_ainvoke_llm.assert_not_awaited()


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_set_context_variables_none(mock_llm_cls):
    reporter = Reporter("basic")
    result = reporter._set_context_variables(None)
    assert result is False
    assert reporter.gen_report_context is None


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_set_context_variables_dict(mock_llm_cls):
    reporter = Reporter("basic")
    ctx = {"foo": "bar"}
    result = reporter._set_context_variables(ctx)
    assert result is True
    assert reporter.gen_report_context == ctx


# ---------------------------------------------------------------------------
# export_outline_without_plans: strip plans from outline for LLM input
# ---------------------------------------------------------------------------

def _make_outline_dict():
    """Build a minimal outline dict with thought, plans, and step_result."""
    return {
        "id": "test-outline",
        "language": "zh-CN",
        "thought": "outline reasoning process",
        "title": "Test Report",
        "sections": [
            {
                "id": "1",
                "title": "Chapter One",
                "description": "desc one",
                "format_requirements": [],
                "is_core_section": True,
                "parent_ids": [],
                "relationships": [],
                "plans": [
                    {
                        "id": "1-1",
                        "language": "zh-CN",
                        "title": "Plan 1",
                        "thought": "plan thought",
                        "is_research_completed": True,
                        "steps": [
                            {
                                "id": "1-1-1",
                                "type": "info_collecting",
                                "title": "Step 1",
                                "description": "collect data",
                                "step_result": "X" * 5_000,
                                "evaluation": "good",
                            },
                        ],
                    },
                ],
                "section_focus": "market_size",
                "focus_dimensions": ["size", "growth"],
            },
            {
                "id": "2",
                "title": "Chapter Two",
                "description": "desc two",
                "plans": [],
                "parent_ids": ["1"],
                "relationships": ["depends on"],
            },
        ],
    }


def test_export_outline_without_plans_strips_plans_from_dict():
    """export_outline_without_plans must remove 'plans' (with step_result) from dict input."""
    outline = _make_outline_dict()
    result = Reporter.export_outline_without_plans(outline)

    assert isinstance(result, dict)
    for sec in result.get("sections", []):
        assert "plans" not in sec or sec["plans"] == []
        assert "step_result" not in str(sec)

    # Title and section metadata are preserved
    assert result["title"] == "Test Report"
    assert result["sections"][0]["title"] == "Chapter One"
    assert result["sections"][0]["description"] == "desc one"
    assert result["sections"][1]["parent_ids"] == ["1"]


def test_export_outline_without_plans_with_empty_input():
    """None / unsupported types should be handled gracefully."""
    assert Reporter.export_outline_without_plans(None) is None
    assert Reporter.export_outline_without_plans("str") == "str"
    assert Reporter.export_outline_without_plans({}) == {}


def test_export_outline_without_plans_preserves_section_metadata():
    """Section metadata (focus, dimensions, parent_ids) must survive stripping."""
    outline = _make_outline_dict()
    result = Reporter.export_outline_without_plans(outline)
    sec0 = result["sections"][0]
    assert sec0["section_focus"] == "market_size"
    assert sec0["focus_dimensions"] == ["size", "growth"]
    assert sec0["is_core_section"] is True


def test_export_outline_without_plans_preserves_thought():
    """thought field should be preserved (not stripped by export_outline_without_plans)."""
    outline = _make_outline_dict()
    result = Reporter.export_outline_without_plans(outline)
    assert result.get("thought") == "outline reasoning process"


def test_export_outline_without_plans_with_outline_object():
    """export_outline_without_plans must handle Outline objects, returning Outline."""
    step = Step(
        type=StepType.INFO_COLLECTING,
        title="Step 1",
        description="desc",
        step_result="R" * 5_000,
        evaluation="eval",
    )
    plan = Plan(
        id="1-1",
        title="Plan 1",
        thought="plan thought",
        is_research_completed=True,
        steps=[step],
    )
    section = Section(
        id="1",
        title="Chapter One",
        description="desc one",
        plans=[plan],
        section_focus="market_size",
        focus_dimensions=["size"],
    )
    outline = Outline(
        thought="T" * 10_000,
        title="Test Report",
        sections=[section],
    )

    result = Reporter.export_outline_without_plans(outline)
    assert isinstance(result, Outline)
    assert result.title == "Test Report"
    # plans must be stripped
    for sec in result.sections:
        assert sec.plans == []
    # step_result must not leak
    assert "R" * 100 not in str(result)
