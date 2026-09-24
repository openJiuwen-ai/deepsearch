"""planner use_material_ids 声明（schema 注入与幻觉 ID 过滤）的单测。"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.planner import (
    Planner,
    create_plan_tool,
    generate_plan,
)

_PLANNER_MODULE = "openjiuwen_deepsearch.algorithm.query_understanding.planner"

test_data = {
    'input': 'test input',
    'section_idx': 1,
    'plan_executed_num': 0,
    'max_plan_executed_num': 3,
}


def _plan_response(use_material_ids):
    return {
        'content': '',
        'role': 'assistant',
        'tool_calls': [{
            'args': {
                'is_research_completed': False,
                'language': 'zh-CN',
                'thought': 'thought',
                'title': 'Test Plan',
                'steps': [{'description': 'D', 'title': 'S', 'type': 'info_collecting'}],
                'use_material_ids': use_material_ids,
            },
            'id': '123',
            'name': 'generate_plan',
            'type': 'tool_call',
        }],
    }


def test_create_plan_tool_without_materials():
    tool = create_plan_tool(test_data, 'planner')
    properties = tool.card.input_params["properties"]
    # 无素材时仍声明字段（提示留空），幻觉输出由过滤逻辑清空而非 invoke 校验失败
    assert "use_material_ids" in properties
    assert "leave it empty" in properties["use_material_ids"]["description"]
    assert tool.use_material_ids == []


def test_create_plan_tool_with_materials():
    tool = create_plan_tool({**test_data, "use_material_ids": ["M1", "M2"]}, 'planner')
    properties = tool.card.input_params["properties"]
    assert "use_material_ids" in properties
    assert "M1, M2" in properties["use_material_ids"]["description"]
    assert tool.use_material_ids == ["M1", "M2"]


def test_generate_plan_function_keeps_material_ids():
    plan = generate_plan("zh-CN", "T", "TH", False, use_material_ids=["M1"])
    assert plan.use_material_ids == ["M1"]
    assert generate_plan("zh-CN", "T", "TH", False).use_material_ids == []


@pytest.mark.asyncio
async def test_planner_filters_hallucinated_material_ids():
    with patch(f'{_PLANNER_MODULE}.llm_context', return_value=Mock()), \
         patch(f'{_PLANNER_MODULE}.ainvoke_llm_with_stats',
               new_callable=AsyncMock, return_value=_plan_response(["M1", "MX"])):
        planner = Planner()
        result = await planner.generate_plan({**test_data, "use_material_ids": ["M1"]})

    assert result.plan_success is True
    assert result.plan.use_material_ids == ["M1"]


@pytest.mark.asyncio
async def test_planner_clears_material_ids_without_declaration():
    with patch(f'{_PLANNER_MODULE}.llm_context', return_value=Mock()), \
         patch(f'{_PLANNER_MODULE}.ainvoke_llm_with_stats',
               new_callable=AsyncMock, return_value=_plan_response(["M1"])):
        planner = Planner()
        result = await planner.generate_plan(dict(test_data))

    assert result.plan_success is True
    assert result.plan.use_material_ids == []


@pytest.mark.asyncio
async def test_planner_uses_bound_material_ids_when_model_omits_them():
    with patch(f'{_PLANNER_MODULE}.llm_context', return_value=Mock()), \
         patch(f'{_PLANNER_MODULE}.ainvoke_llm_with_stats',
               new_callable=AsyncMock, return_value=_plan_response([])):
        planner = Planner()
        result = await planner.generate_plan({
            **test_data,
            "use_material_ids": ["M1"],
            "bound_material_ids": ["M1"],
        })

    assert result.plan_success is True
    assert result.plan.use_material_ids == ["M1"]
