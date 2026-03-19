import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import (
    UserFeedbackActionCategory,
    UserInputActionMapping,
    SynonymRewriteActionSubcategory,
    UserFeedbackRewriteStreamResult,
)
from openjiuwen_deepsearch.common.exception import CustomRuntimeException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor import (
    UserFeedbackProcessor,
)
from openjiuwen_deepsearch.utils.common_utils.stream_utils import StreamEvent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class TestParseFeedback:
    @pytest.mark.parametrize(
        ("raw_input", "expected_action", "expected_selected_text"),
        [
            (
                json.dumps(
                    {
                        "action": "expand",
                        "selected_text": "原文",
                        "start_offset": 10,
                        "end_offset": 12,
                        "user_instruction": "扩写",
                    }
                ),
                "expand",
                "原文",
            ),
            (json.dumps({"action": "finish"}), "finish", None),
        ],
    )
    def test_parse_valid_requests(self, raw_input, expected_action, expected_selected_text):
        data = UserFeedbackProcessor.parse_feedback(raw_input)
        assert data["action"] == expected_action
        if expected_selected_text is not None:
            assert data["selected_text"] == expected_selected_text

    @pytest.mark.parametrize(
        ("raw_input", "expected_error_code", "message_fragment"),
        [
            ("not json", StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.code, "Expecting value"),
            ("1", StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.code, "expected JSON object"),
            (json.dumps({"selected_text": "text"}), StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code, "action"),
        ],
    )
    def test_parse_invalid_requests(self, raw_input, expected_error_code, message_fragment):
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.parse_feedback(raw_input)

        assert exc_info.value.error_code == expected_error_code
        assert message_fragment in exc_info.value.message


class TestValidate:
    def test_valid_input(self):
        report_content = "0123456789原文0123456789"
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 10,
            "end_offset": 12,
        }

        assert UserFeedbackProcessor.validate(feedback, report_content, max_text_length=2000) is None

    @pytest.mark.parametrize(
        ("feedback", "report_content", "max_text_length", "expected_error_code"),
        [
            (
                {
                    "action": "expand",
                    "selected_text": "a" * 100,
                    "start_offset": 0,
                    "end_offset": 100,
                },
                "a" * 100,
                50,
                StatusCode.USER_FEEDBACK_PROCESSOR_TEXT_TOO_LONG.code,
            ),
            (
                {
                    "action": "expand",
                    "selected_text": "不匹配的文本",
                    "start_offset": 0,
                    "end_offset": 6,
                },
                "实际的报告内容",
                2000,
                StatusCode.USER_FEEDBACK_PROCESSOR_OFFSET_MISMATCH.code,
            ),
            (
                {
                    "action": "unknown",
                    "selected_text": "text",
                    "start_offset": 0,
                    "end_offset": 4,
                },
                "text",
                2000,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
            ),
            (
                {
                    "action": "expand",
                    "selected_text": None,
                    "start_offset": "0",
                    "end_offset": 4,
                },
                "text",
                2000,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
            ),
        ],
    )
    def test_invalid_input(self, feedback, report_content, max_text_length, expected_error_code):
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate(feedback, report_content, max_text_length=max_text_length)

        assert exc_info.value.error_code == expected_error_code

    def test_finish_action_skips_offset_validation(self):
        assert UserFeedbackProcessor.validate({"action": "finish"}, "any report content", max_text_length=2000) is None


class TestUserFeedbackProcessorDispatch:
    @pytest.fixture
    def processor(self):
        return UserFeedbackProcessor(llm_model_name="mock_model")

    @pytest.mark.asyncio
    async def test_execute_dispatches_rewrite_actions_to_synonym_rewrite_service(self, processor):
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
            "user_instruction": "",
        }

        with patch.object(processor._synonym_rewriter, "synonym_rewrite", new_callable=AsyncMock) as mock_synonym_rewrite:
            mock_synonym_rewrite.return_value = {
                "new_report": "改写后的文本后续内容",
                "rewritten_text": "改写后的文本",
                "start_offset": 0,
                "new_end_offset": 6,
                "updated_messages": {"code": 0, "msg": "success", "data": []},
                "updated_infer_messages": [],
            }

            result = await processor.execute(
                feedback=feedback,
                final_result={
                    "response_content": "原文后续内容",
                    "citation_messages": {},
                    "infer_messages": [],
                },
                language="zh-CN",
            )

        assert result == {
            "new_report": "改写后的文本后续内容",
            "original_text_clean": "原文",
            "rewritten_text": "改写后的文本",
            "start_offset": 0,
            "new_end_offset": 6,
            "updated_citation_messages": {"code": 0, "msg": "success", "data": []},
            "updated_infer_messages": [],
        }
        mock_synonym_rewrite.assert_awaited_once_with(
            feedback=feedback,
            report_content="原文后续内容",
            citation_messages={},
            language="zh-CN",
            infer_messages=[],
        )

    @pytest.mark.asyncio
    async def test_execute_rejects_unsupported_action(self, processor):
        with pytest.raises(CustomValueException) as exc_info:
            await processor.execute(
                feedback={"action": "unsupported"},
                final_result={"response_content": "report"},
                language="zh-CN",
            )

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code

    def test_build_stream_result_returns_none_for_non_rewrite_action(self):
        feedback = {
            "action": "finish",
            "selected_text": "",
            "start_offset": 0,
            "end_offset": 0,
        }
        action_result = {
            "rewritten_text": "",
            "start_offset": 0,
            "new_end_offset": 0,
        }

        assert UserFeedbackProcessor.build_stream_result(feedback, action_result) is None

    def test_build_stream_result_uses_rewrite_error_errmsg_for_invalid_rewrite_mapping(self):
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
        }
        action_result = {
            "rewritten_text": "改写",
            "start_offset": 0,
            "new_end_offset": 2,
        }

        with patch(
            "openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor.resolve_user_input_action",
            return_value=UserInputActionMapping(
                action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
                action_subcategory=None,
            ),
        ):
            with pytest.raises(CustomRuntimeException) as exc_info:
                UserFeedbackProcessor.build_stream_result(feedback, action_result)

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
        assert exc_info.value.message == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(
            e="Rewrite stream result requires synonym_rewrite subcategory, got action: expand"
        )


class TestSendResult:
    @pytest.mark.asyncio
    async def test_send_result_outputs_full_rewrite_metadata_with_updated_final_result_field(self):
        session = MagicMock()
        session.write_custom_stream = AsyncMock()

        feedback = {"action": "expand"}
        result = UserFeedbackRewriteStreamResult(
                original_text="原始文本",
                original_start_offset=10,
                original_end_offset=14,
                rewritten_text="改写后的文本",
                rewritten_start_offset=10,
                rewritten_end_offset=16,
                action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
                action_subcategory=SynonymRewriteActionSubcategory.EXPAND,
            )
        final_result = {
            "response_content": "完整改写后的报告",
            "citation_messages": {"code": 0, "msg": "success", "data": []},
            "infer_messages": [{"id": 0, "content": "保留推理"}],
            "exception_info": "[212405] stale error",
            "warning_info": "ignored",
        }

        await UserFeedbackProcessor.send_result(
            session=session,
            feedback=feedback,
            result=result,
            final_result=final_result,
        )

        session.write_custom_stream.assert_awaited_once()
        payload = session.write_custom_stream.await_args.args[0]
        assert payload["agent"] == NodeId.USER_FEEDBACK_PROCESSOR.value
        assert payload["event"] == StreamEvent.SUMMARY_RESPONSE.value

        content = json.loads(payload["content"])
        assert content == {
            "original_text": "原始文本",
            "original_start_offset": 10,
            "original_end_offset": 14,
            "rewritten_text": "改写后的文本",
            "rewritten_start_offset": 10,
            "rewritten_end_offset": 16,
            "action_category": "synonym_rewrite",
            "action_subcategory": "expand",
            "final_result": {
                "response_content": "完整改写后的报告",
                "citation_messages": {"code": 0, "msg": "success", "data": []},
                "infer_messages": [{"id": 0, "content": "保留推理"}],
            },
        }

    @pytest.mark.asyncio
    async def test_send_result_noops_for_finish_category(self):
        session = MagicMock()
        session.write_custom_stream = AsyncMock()

        await UserFeedbackProcessor.send_result(
            session=session,
            feedback={"action": "finish"},
            result=None,
            final_result=None,
        )

        session.write_custom_stream.assert_not_awaited()


class TestSendError:
    @pytest.mark.asyncio
    async def test_send_error_outputs_single_error_field_from_custom_exception(self):
        session = MagicMock()
        session.write_custom_stream = AsyncMock()
        error = CustomValueException(
            StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
            StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action="bad"),
        )

        await UserFeedbackProcessor.send_error(session, error)

        payload = session.write_custom_stream.await_args.args[0]
        content = json.loads(payload["content"])
        assert content == {"error": str(error)}
