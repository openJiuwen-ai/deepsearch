import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import (
    UserFeedbackActionCategory,
    SynonymRewriteActionSubcategory,
    UserFeedbackRewriteStreamResult,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import UserFeedbackProcessorNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Report
from openjiuwen_deepsearch.utils.common_utils.stream_utils import StreamEvent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


ALGO_CLASS_PATH = "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.UserFeedbackProcessor"
NODE_MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes"


def make_mock_session(config_overrides=None, search_context_overrides=None):
    session = MagicMock()
    session.write_custom_stream = AsyncMock()
    session.interact = AsyncMock()

    config = {
        "user_feedback_processor_enable": True,
        "user_feedback_processor_max_interactions": 3,
        "user_feedback_processor_max_text_length": 2000,
        "workflow_feedback_mode": "web",
        "workflow_human_in_the_loop": True,
    }
    if config_overrides:
        config.update(config_overrides)

    report = Report(
        id="test",
        checked_trace_source_report_content="这是一段测试报告[[1]](https://a.com)内容结束",
        checked_trace_source_datas=[
            {
                "url": "https://a.com",
                "valid": True,
                "reference_index": 1,
                "citation_start_offset": 8,
                "citation_end_offset": 28,
            }
        ],
    )

    search_context = {
        "language": "zh-CN",
        "current_report": report,
        "feedback_interaction_count": 0,
        "rewrite_history": [],
        "final_result": {
            "response_content": report.checked_trace_source_report_content,
            "citation_messages": {
                "code": 0,
                "msg": "success",
                "data": [
                    {
                        "id": 0,
                        "reference_index": 1,
                        "citation_start_offset": 8,
                        "citation_end_offset": 28,
                    }
                ],
            },
            "infer_messages": [],
        },
    }
    if search_context_overrides:
        search_context.update(search_context_overrides)

    def get_global_state(key):
        parts = key.split(".", 1)
        if parts[0] == "config":
            return config.get(parts[1]) if len(parts) > 1 else config
        if parts[0] == "search_context":
            if len(parts) > 1:
                val = search_context
                for p in parts[1].split("."):
                    if isinstance(val, dict):
                        val = val.get(p)
                    else:
                        val = getattr(val, p, None)
                return val
            return search_context
        return None

    session.get_global_state = MagicMock(side_effect=get_global_state)
    session.update_global_state = MagicMock()
    return session


class TestUserFeedbackProcessorNode:
    @pytest.fixture
    def node(self):
        return UserFeedbackProcessorNode()

    def test_pre_handle_disabled(self, node):
        session = make_mock_session(config_overrides={"user_feedback_processor_enable": False})
        result = node._pre_handle(None, session, None)
        assert result["disabled"] is True

    def test_pre_handle_enabled(self, node):
        session = make_mock_session()
        result = node._pre_handle(None, session, None)
        assert result["disabled"] is False
        assert result["max_interactions"] == 3
        assert result["max_text_length"] == 2000

    @pytest.mark.asyncio
    async def test_do_invoke_disabled_goes_to_end(self, node):
        session = make_mock_session(config_overrides={"user_feedback_processor_enable": False})
        result = await node._do_invoke(None, session, None)
        assert result["next_node"] == NodeId.END.value

    @pytest.mark.asyncio
    async def test_do_invoke_finish_action(self, node):
        session = make_mock_session()
        session.interact.return_value = json.dumps({"action": "finish"})
        result = await node._do_invoke(None, session, None)
        assert result["next_node"] == NodeId.END.value
        assert not any(
            call.args[0] == {"search_context.feedback_interaction_count": 1}
            for call in session.update_global_state.call_args_list
        )
        end_events = [
            call.args[0]
            for call in session.write_custom_stream.await_args_list
            if call.args[0]["event"] == StreamEvent.USER_INPUT_ENDED.value
        ]
        assert len(end_events) == 1
        assert end_events[0]["agent"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        assert end_events[0]["content"] == "User feedback finished."

    @pytest.mark.asyncio
    async def test_do_invoke_parse_error_loops_back(self, node):
        session = make_mock_session()
        session.interact.return_value = "not valid json"
        with patch(f"{ALGO_CLASS_PATH}.send_error", new_callable=AsyncMock) as mock_send_error:
            result = await node._do_invoke(None, session, None)
        assert result["next_node"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        mock_send_error.assert_awaited_once()
        session.update_global_state.assert_any_call({"search_context.feedback_interaction_count": 1})
        error = mock_send_error.await_args.args[1]
        session.update_global_state.assert_any_call({"search_context.final_result.exception_info": str(error)})

    @pytest.mark.asyncio
    async def test_do_invoke_validation_error_loops_back(self, node):
        session = make_mock_session()
        feedback = {
            "action": "expand",
            "selected_text": "不匹配的内容",
            "start_offset": 0,
            "end_offset": 6,
            "user_instruction": "",
        }
        session.interact.return_value = json.dumps(feedback)

        with patch(f"{ALGO_CLASS_PATH}.send_error", new_callable=AsyncMock) as mock_send_error:
            result = await node._do_invoke(None, session, None)

        assert result["next_node"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        mock_send_error.assert_awaited_once()
        session.update_global_state.assert_any_call({"search_context.feedback_interaction_count": 1})
        error = mock_send_error.await_args.args[1]
        session.update_global_state.assert_any_call({"search_context.final_result.exception_info": str(error)})

    @pytest.mark.asyncio
    async def test_do_invoke_max_interactions_reached(self, node):
        session = make_mock_session(
            config_overrides={"user_feedback_processor_max_interactions": 1},
            search_context_overrides={"feedback_interaction_count": 1},
        )
        result = await node._do_invoke(None, session, None)
        assert result["next_node"] == NodeId.END.value
        end_events = [
            call.args[0]
            for call in session.write_custom_stream.await_args_list
            if call.args[0]["event"] == StreamEvent.USER_INPUT_ENDED.value
        ]
        assert len(end_events) == 1
        assert end_events[0]["agent"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        assert end_events[0]["content"] == "Maximum interaction rounds reached."

    @pytest.mark.asyncio
    async def test_do_invoke_first_interaction_streams_full_report(self, node):
        session = make_mock_session()
        session.interact.return_value = json.dumps({"action": "finish"})

        with patch(f"{NODE_MODULE_PATH}.custom_stream_output", new_callable=AsyncMock) as mock_stream_output:
            result = await node._do_invoke(None, session, None)

        assert result["next_node"] == NodeId.END.value
        mock_stream_output.assert_awaited_once()
        _, _, streamed_content, streamed_node_id = mock_stream_output.await_args.args
        assert json.loads(streamed_content) == session.get_global_state("search_context.final_result")
        assert streamed_node_id == NodeId.USER_FEEDBACK_PROCESSOR.value

    @pytest.mark.asyncio
    async def test_do_invoke_execute_exception_loops_back(self, node):
        session = make_mock_session()
        feedback = {
            "action": "expand",
            "selected_text": "这是一段",
            "start_offset": 0,
            "end_offset": 4,
            "user_instruction": "",
        }
        session.interact.return_value = json.dumps(feedback)

        with patch(f"{ALGO_CLASS_PATH}.execute", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            with patch(f"{ALGO_CLASS_PATH}.send_error", new_callable=AsyncMock) as mock_send_error:
                result = await node._do_invoke(None, session, None)

        assert result["next_node"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        mock_send_error.assert_awaited_once()
        error = mock_send_error.await_args.args[1]
        assert isinstance(error, CustomValueException)
        assert error.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
        session.update_global_state.assert_any_call({"search_context.feedback_interaction_count": 1})
        session.update_global_state.assert_any_call({"search_context.final_result.exception_info": str(error)})

    @pytest.mark.asyncio
    async def test_do_invoke_rewrite_success_updates_session_state(self, node):
        session = make_mock_session(search_context_overrides={
            "final_result": {
                "response_content": "这是一段测试报告[[1]](https://a.com)内容结束",
                "citation_messages": {
                    "code": 0,
                    "msg": "success",
                    "data": [
                        {
                            "id": 0,
                            "reference_index": 1,
                            "citation_start_offset": 8,
                            "citation_end_offset": 28,
                        }
                    ],
                },
                "infer_messages": [],
                "exception_info": "[212405] Rewrite failed: old error\t",
            }
        })
        feedback = {
            "action": "expand",
            "selected_text": "这是一段",
            "start_offset": 0,
            "end_offset": 4,
            "user_instruction": "",
        }
        session.interact.return_value = json.dumps(feedback)

        execute_return = {
            "new_report": "扩写后的测试报告内容",
            "original_text_clean": "这是一段",
            "rewritten_text": "扩写后的测试报告内容",
            "start_offset": 0,
            "new_end_offset": 10,
            "updated_citation_messages": {},
            "updated_infer_messages": [{"id": 0, "content": "保留推理"}],
        }

        with patch(f"{ALGO_CLASS_PATH}.execute", new_callable=AsyncMock, return_value=execute_return):
            with patch(f"{ALGO_CLASS_PATH}.send_result", new_callable=AsyncMock) as mock_send_result:
                with patch(f"{NODE_MODULE_PATH}.add_debug_log_wrapper"):
                    result = await node._do_invoke(None, session, None)

        assert result["next_node"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        mock_send_result.assert_awaited_once()
        kwargs = mock_send_result.await_args.kwargs
        assert kwargs["session"] is session
        assert kwargs["feedback"] == feedback
        assert kwargs["result"] == UserFeedbackRewriteStreamResult(
            original_text=feedback["selected_text"],
            original_start_offset=feedback["start_offset"],
            original_end_offset=feedback["end_offset"],
            rewritten_text=execute_return["rewritten_text"],
            rewritten_start_offset=execute_return["start_offset"],
            rewritten_end_offset=execute_return["new_end_offset"],
            action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
            action_subcategory=SynonymRewriteActionSubcategory.EXPAND,
        )
        assert kwargs["final_result"]["response_content"] == execute_return["new_report"]
        assert kwargs["final_result"]["citation_messages"] == execute_return["updated_citation_messages"]
        assert kwargs["final_result"]["infer_messages"] == execute_return["updated_infer_messages"]

        session.update_global_state.assert_any_call(
            {"search_context.final_result.response_content": execute_return["new_report"]}
        )
        session.update_global_state.assert_any_call(
            {"search_context.final_result.citation_messages": execute_return["updated_citation_messages"]}
        )
        session.update_global_state.assert_any_call(
            {"search_context.final_result.infer_messages": execute_return["updated_infer_messages"]}
        )
        session.update_global_state.assert_any_call({"search_context.feedback_interaction_count": 1})

        rewrite_history_updates = [
            call.args[0]
            for call in session.update_global_state.call_args_list
            if "search_context.rewrite_history" in call.args[0]
        ]
        assert rewrite_history_updates
        assert rewrite_history_updates[-1]["search_context.rewrite_history"] == [
            {
                "action": "expand",
                "selected_text": "这是一段",
                "selected_text_clean": "这是一段",
                "original_start_offset": 0,
                "original_end_offset": 4,
                "rewritten_text": execute_return["rewritten_text"],
                "rewritten_start_offset": 0,
                "rewritten_end_offset": execute_return["new_end_offset"],
                "user_instruction": "",
            }
        ]
