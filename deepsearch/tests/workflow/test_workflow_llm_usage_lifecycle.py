import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    local_search_context,
    session_context,
    web_search_context,
)
from tests.utils.mock_config import get_default_agent_config


@pytest.mark.asyncio
async def test_run_keeps_workflow_llm_usage_for_non_terminal_stream():
    """验证未到 ALL END 的 run 不会清理 conversation 级 token 累计。"""
    agent = DeepresearchAgent()
    agent.agent.release_session = AsyncMock()
    agent._release_checkpointer_session = AsyncMock()
    agent_config = get_default_agent_config()
    agent_config["stats_info_llm"] = True
    conversation_id = "workflow-token-lifecycle"

    async def _fake_consume_stream_chunks(*args, **kwargs):
        """模拟一次等待用户交互的非终态流式执行。"""
        yield '{"event":"waiting_user_input"}', False, {}

    def _fake_initialize_tools(_):
        """返回可安全 reset 的 context token。"""
        return web_search_context.set({}), local_search_context.set({})

    with patch.object(agent, "_initialize_tools", side_effect=_fake_initialize_tools), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj",
        return_value=object(),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchAgent._consume_stream_chunks",
        side_effect=_fake_consume_stream_chunks,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.pop_workflow_llm_usage",
    ) as mock_pop_usage, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.record_interface_log",
    ):
        outputs = []
        async for item in agent.run(
            message="hello",
            conversation_id=conversation_id,
            agent_config=agent_config,
            report_template="",
            interrupt_feedback="",
        ):
            outputs.append(item)

    assert outputs == ['{"event":"waiting_user_input"}']
    mock_pop_usage.assert_not_called()


@pytest.mark.asyncio
async def test_run_pops_workflow_llm_usage_when_stream_reaches_all_end():
    """验证到达 ALL END 时仍会清理 conversation 级 token 累计。"""
    agent = DeepresearchAgent()
    agent.agent.release_session = AsyncMock()
    agent._release_checkpointer_session = AsyncMock()
    agent_config = get_default_agent_config()
    agent_config["stats_info_llm"] = True
    conversation_id = "workflow-token-all-end"

    async def _fake_consume_stream_chunks(*args, **kwargs):
        """模拟一次到达终态的流式执行。"""
        yield '{"event":"summary_response"}', True, {}

    def _fake_initialize_tools(_):
        """返回可安全 reset 的 context token。"""
        return web_search_context.set({}), local_search_context.set({})

    with patch.object(agent, "_initialize_tools", side_effect=_fake_initialize_tools), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj",
        return_value=object(),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchAgent._consume_stream_chunks",
        side_effect=_fake_consume_stream_chunks,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.pop_workflow_llm_usage",
    ) as mock_pop_usage, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.record_interface_log",
    ):
        outputs = []
        async for item in agent.run(
            message="hello",
            conversation_id=conversation_id,
            agent_config=agent_config,
            report_template="",
            interrupt_feedback="",
        ):
            outputs.append(item)

    assert outputs == ['{"event":"summary_response"}']
    mock_pop_usage.assert_called_once_with(conversation_id)


@pytest.mark.asyncio
async def test_run_uses_persisted_workflow_usage_snapshot_when_local_usage_empty():
    """验证恢复场景异常退出时会回退 session 中已持久化的 token 快照。"""
    agent = DeepresearchAgent()
    agent.agent.release_session = AsyncMock()
    agent._release_checkpointer_session = AsyncMock()
    agent_config = get_default_agent_config()
    agent_config["stats_info_llm"] = True
    conversation_id = "workflow-token-resume"
    empty_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,
        "agent_name_token_usage": [],
    }
    snapshot_usage = {
        "input_tokens": 12,
        "output_tokens": 9,
        "total_tokens": 21,
        "llm_call_count": 4,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": 12,
                "output_tokens": 9,
                "total_tokens": 21,
                "llm_call_count": 4,
            }
        ],
    }

    async def _fake_streaming(*args, **kwargs):
        """模拟流式执行异常，触发 run 的异常分支。"""
        raise RuntimeError("mock streaming error")
        yield  # pragma: no cover

    def _fake_initialize_tools(_):
        """返回可安全 reset 的 context token。"""
        return web_search_context.set({}), local_search_context.set({})

    class _FakeSession:
        """模拟 session 快照读取对象。"""

        def get_global_state(self, key):
            """读取持久化快照。"""
            if key == "search_context.final_result.workflow_llm_token_usage":
                return snapshot_usage
            return None

    session_token = session_context.set(_FakeSession())
    try:
        with patch.object(agent, "_initialize_tools", side_effect=_fake_initialize_tools), patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj",
            return_value=object(),
        ), patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_agent_streaming",
            side_effect=_fake_streaming,
        ), patch(
            "openjiuwen_deepsearch.utils.common_utils.llm_utils.get_workflow_llm_usage",
            return_value=empty_usage,
        ), patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.pop_workflow_llm_usage",
        ) as mock_pop_usage, patch(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.record_interface_log",
        ):
            outputs = []
            async for item in agent.run(
                message='{"action":"finish"}',
                conversation_id=conversation_id,
                agent_config=agent_config,
                report_template="",
                interrupt_feedback="",
            ):
                outputs.append(item)
    finally:
        session_context.reset(session_token)

    assert len(outputs) == 2
    error_payload = json.loads(outputs[0])
    error_content = json.loads(error_payload["content"])
    assert error_content["workflow_llm_token_usage"] == snapshot_usage
    mock_pop_usage.assert_called_once_with(conversation_id)
