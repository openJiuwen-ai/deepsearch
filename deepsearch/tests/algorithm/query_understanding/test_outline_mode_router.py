"""测试大纲模式路由工具函数。"""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from openjiuwen_deepsearch.algorithm.query_understanding import outline_mode_router as omr
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import SearchContext
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


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


def test_search_context_outline_execution_method_has_state_contract():
    """大纲实际执行方式只允许空值、parallel 或 dependency_driving；hybrid 只作为外部入口。"""
    assert SearchContext().outline_execution_method == ""
    assert SearchContext(
        outline_execution_method=ExecutionMethod.PARALLEL.value
    ).outline_execution_method == ExecutionMethod.PARALLEL.value
    assert SearchContext(
        outline_execution_method=ExecutionMethod.DEPENDENCY_DRIVING.value
    ).outline_execution_method == ExecutionMethod.DEPENDENCY_DRIVING.value

    with pytest.raises(ValidationError):
        SearchContext(outline_execution_method=ExecutionMethod.HYBRID.value)


@pytest.mark.asyncio
async def test_route_empty_question_no_llm_defaults_parallel():
    """query 为空时不调用 LLM，直接兜底为普通并行模式。"""
    with patch.object(omr, "ainvoke_llm_with_stats", new_callable=AsyncMock) as mock_llm:
        out = await omr.route_outline_execution_method("  \n", "basic")

    assert out == ExecutionMethod.PARALLEL.value
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_route_respects_parallel_output():
    """LLM 返回 parallel 时选择普通并行模式。"""
    token = llm_context.set({"basic": {"model": object(), "model_name": "basic"}})
    try:
        with patch.object(
            omr,
            "ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"content": ExecutionMethod.PARALLEL.value}),
        ):
            out = await omr.route_outline_execution_method("市场格局综述", "basic")
    finally:
        llm_context.reset(token)

    assert out == ExecutionMethod.PARALLEL.value


@pytest.mark.asyncio
async def test_route_respects_dependency_driving_output():
    """LLM 返回 dependency_driving 时选择依赖驱动模式。"""
    token = llm_context.set({"basic": {"model": object(), "model_name": "basic"}})
    try:
        with patch.object(
            omr,
            "ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"content": ExecutionMethod.DEPENDENCY_DRIVING.value}),
        ):
            out = await omr.route_outline_execution_method("先诊断问题再提出行动方案", "basic")
    finally:
        llm_context.reset(token)

    assert out == ExecutionMethod.DEPENDENCY_DRIVING.value


@pytest.mark.asyncio
async def test_route_invalid_model_output_defaults_parallel():
    """LLM 输出非法内容时兜底为普通并行模式。"""
    token = llm_context.set({"basic": {"model": object(), "model_name": "basic"}})
    try:
        with patch.object(
            omr,
            "ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"content": "choose parallel"}),
        ):
            out = await omr.route_outline_execution_method("q", "basic")
    finally:
        llm_context.reset(token)

    assert out == ExecutionMethod.PARALLEL.value


@pytest.mark.asyncio
async def test_route_llm_exception_defaults_parallel():
    """LLM 调用异常时兜底为普通并行模式。"""
    token = llm_context.set({"basic": {"model": object(), "model_name": "basic"}})
    try:
        with patch.object(
            omr,
            "ainvoke_llm_with_stats",
            new=AsyncMock(side_effect=RuntimeError("api down")),
        ):
            out = await omr.route_outline_execution_method("q", "basic")
    finally:
        llm_context.reset(token)

    assert out == ExecutionMethod.PARALLEL.value


@pytest.mark.asyncio
async def test_route_messages_and_call_options():
    """确认 router 使用系统提示词、用户 query 和独立的 agent_name 调用 LLM。"""
    seen = {}

    async def capture(_llm, messages, **kwargs):
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return {"content": ExecutionMethod.PARALLEL.value}

    token = llm_context.set({"basic": {"model": object(), "model_name": "basic"}})
    try:
        with patch.object(omr, "ainvoke_llm_with_stats", side_effect=capture):
            await omr.route_outline_execution_method("用户问题", "basic")
    finally:
        llm_context.reset(token)

    assert seen["messages"][0]["role"] == "system"
    assert omr.OUTLINE_MODE_ROUTER_SYSTEM_PROMPT in seen["messages"][0]["content"]
    assert seen["messages"][1] == {"role": "user", "content": "用户问题"}
    assert seen["kwargs"]["llm_type"] == "basic"
    assert seen["kwargs"]["agent_name"] == AgentLlmName.OUTLINE_MODE_ROUTER.value
    assert seen["kwargs"]["tools"] is None
