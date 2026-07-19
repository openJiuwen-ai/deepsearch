"""测试大纲模式路由工具函数。"""

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.utils import outline_mode_router as omr


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", None),
        ("   ", None),
        ("parallel", ExecutionMethod.PARALLEL.value),
        ("\nparallel\n", ExecutionMethod.PARALLEL.value),
        ("dependency_driving", ExecutionMethod.DEPENDENCY_DRIVING.value),
        ("parallel.", None),
        ("choose parallel", None),
        ("dependency", None),
    ],
)
def test_parse_outline_execution_method_strictly(text, expected):
    """严格解析 router 输出，只有完整合法标签才会被接受。"""
    assert omr.parse_outline_execution_method(text) == expected


def test_system_prompt_file_loaded():
    """确认 router prompt 文件已被加载。"""
    assert isinstance(omr.OUTLINE_MODE_ROUTER_SYSTEM_PROMPT, str)
    assert "parallel" in omr.OUTLINE_MODE_ROUTER_SYSTEM_PROMPT
    assert "dependency_driving" in omr.OUTLINE_MODE_ROUTER_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_route_empty_question_no_llm_defaults_parallel():
    """query 为空时不调用 LLM，直接兜底为普通并行模式。"""
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
    ) as mock_llm:
        out = await omr.route_outline_execution_method("  \n", {"model": object(), "model_name": "m"})

    assert out == ExecutionMethod.PARALLEL.value
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_route_respects_parallel_output():
    """LLM 返回 parallel 时选择普通并行模式。"""
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(return_value={"content": "parallel"}),
    ):
        out = await omr.route_outline_execution_method("市场格局综述", {"model": object(), "model_name": "m"})

    assert out == ExecutionMethod.PARALLEL.value


@pytest.mark.asyncio
async def test_route_respects_dependency_driving_output():
    """LLM 返回 dependency_driving 时选择依赖驱动模式。"""
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(return_value={"content": "dependency_driving"}),
    ):
        out = await omr.route_outline_execution_method("先诊断问题再提出行动方案", {"model": object(), "model_name": "m"})

    assert out == ExecutionMethod.DEPENDENCY_DRIVING.value


@pytest.mark.asyncio
async def test_route_invalid_model_output_defaults_parallel():
    """LLM 输出非法内容时兜底为普通并行模式。"""
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(return_value={"content": "choose parallel"}),
    ):
        out = await omr.route_outline_execution_method("q", {"model": object(), "model_name": "m"})

    assert out == ExecutionMethod.PARALLEL.value


@pytest.mark.asyncio
async def test_route_llm_exception_defaults_parallel():
    """LLM 调用异常时兜底为普通并行模式。"""
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        out = await omr.route_outline_execution_method("q", {"model": object(), "model_name": "m"})

    assert out == ExecutionMethod.PARALLEL.value


@pytest.mark.asyncio
async def test_route_messages_and_call_options():
    """确认 router 使用系统提示词、用户 query 和独立的 agent_name 调用 LLM。"""
    seen = {}

    async def capture(_llm, messages, **kwargs):
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return {"content": "parallel"}

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        side_effect=capture,
    ):
        await omr.route_outline_execution_method("用户问题", {"model": object(), "model_name": "m"}, extra_body={"k": 1})

    assert seen["messages"][0]["role"] == "system"
    assert omr.OUTLINE_MODE_ROUTER_SYSTEM_PROMPT in seen["messages"][0]["content"]
    assert seen["messages"][1] == {"role": "user", "content": "用户问题"}
    assert seen["kwargs"]["llm_type"] == "basic"
    assert seen["kwargs"]["agent_name"] == "outline_mode_router"
    assert seen["kwargs"]["tools"] is None
    assert seen["kwargs"]["extra_body"] == {"k": 1}