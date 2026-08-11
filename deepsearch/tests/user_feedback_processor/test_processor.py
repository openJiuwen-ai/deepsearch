import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import (
    ResolvedUserAction,
    SupplementarySearchActionSubcategory,
    UserFeedbackActionCategory,
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
            (
                json.dumps(
                    {
                        "action": "expand",
                        "selected_text": "原文",
                        "start_offset": 10,
                        "end_offset": 12,
                        "user_instruction": "扩写",
                        "rewrite_scope": "",
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
        assert data["rewrite_scope"] == "selected_only"
        if expected_selected_text is not None:
            assert data["selected_text"] == expected_selected_text

    def test_parse_valid_sync_request(self):
        data = UserFeedbackProcessor.parse_feedback(
            json.dumps({"action": "sync", "selected_text": "前端完整报告"}, ensure_ascii=False)
        )

        assert data["action"] == "sync"
        assert data["selected_text"] == "前端完整报告"
        assert data["rewrite_scope"] == "selected_only"

    @pytest.mark.parametrize(
        "action_value",
        [
            pytest.param(None, id="missing"),
            pytest.param("", id="empty_string"),
        ],
    )
    def test_parse_feedback_rejects_invalid_action(self, action_value):
        payload = {
            "selected_text": "原文",
            "start_offset": 10,
            "end_offset": 12,
            "user_instruction": "补充这一段的信息",
        }
        if action_value is not None:
            payload["action"] = action_value
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.parse_feedback(json.dumps(payload))

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code

    @pytest.mark.parametrize(
        ("raw_input", "expected_error_code", "message_fragment"),
        [
            ("not json", StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.code, "Expecting value"),
            ("1", StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.code, "expected JSON object"),
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

        assert UserFeedbackProcessor.validate(feedback, report_content) is None

    @pytest.mark.parametrize(
        ("feedback", "report_content", "expected_error_code"),
        [
            (
                {
                    "action": "expand",
                    "selected_text": "不匹配的文本",
                    "start_offset": 0,
                    "end_offset": 6,
                },
                "实际的报告内容",
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
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
            ),
            (
                {
                    "action": "expand",
                    "selected_text": "text",
                    "start_offset": 0,
                    "end_offset": 4,
                    "user_instruction": ["not", "a", "string"],
                },
                "text",
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
            ),
            (
                {
                    "action": "expand",
                    "selected_text": "text",
                    "start_offset": 0,
                    "end_offset": 4,
                    "rewrite_scope": 123,
                },
                "text",
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
            ),
            (
                {
                    "action": "supplementary_search",
                    "selected_text": "text",
                    "start_offset": 0,
                    "end_offset": 4,
                    "rewrite_scope": 123,
                },
                "text",
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
            ),
        ],
    )
    def test_invalid_input(self, feedback, report_content, expected_error_code):
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate(feedback, report_content)

        assert exc_info.value.error_code == expected_error_code

    def test_finish_action_skips_offset_validation(self):
        assert UserFeedbackProcessor.validate({"action": "finish"}, "any report content") is None

    def test_validate_sync_skips_offset_validation(self):
        feedback = {"action": "sync", "selected_text": "前端完整报告"}
        assert UserFeedbackProcessor.validate(feedback, "旧报告") is None

    def test_validate_sync_requires_selected_text(self):
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate({"action": "sync"}, "旧报告")

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code
        assert "selected_text" in exc_info.value.message

    def test_validate_sync_rejects_empty_selected_text(self):
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate({"action": "sync", "selected_text": ""}, "旧报告")

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code
        assert "selected_text" in exc_info.value.message

    def test_validate_truth_verification_requires_valid_offset_alignment(self):
        report_content = "第一行结论\n第二行补充"
        feedback = {
            "action": "truth_verification",
            "selected_text": "第一行结论\n第二行补充",
            "start_offset": 0,
            "end_offset": len("第一行结论\n第二行补充"),
        }
        assert UserFeedbackProcessor.validate(feedback, report_content) is None

    def test_validate_expand_no_longer_rejects_long_selection(self):
        report_content = "a" * 5000
        feedback = {
            "action": "expand",
            "selected_text": report_content,
            "start_offset": 0,
            "end_offset": len(report_content),
        }
        assert UserFeedbackProcessor.validate(feedback, report_content) is None

    @pytest.mark.parametrize(
        "extra_fields",
        [
            {"user_instruction": 123},
            {"rewrite_scope": 123},
        ],
    )
    def test_finish_action_rejects_non_string_optional_fields(self, extra_fields):
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate(
                {"action": "finish", **extra_fields},
                "any report content",
            )
        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code

    def test_validate_rejects_supplementary_search_with_invalid_rewrite_scope(self):
        report_content = "原文"
        feedback = {
            "action": "supplementary_search",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
            "rewrite_scope": "invalid_scope",
        }
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate(feedback, report_content)

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_REWRITE_SCOPE.code

    def test_validate_accepts_expand_with_non_enum_rewrite_scope(self):
        report_content = "0123456789原文0123456789"
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 10,
            "end_offset": 12,
            "rewrite_scope": "ignored_for_non_supplementary",
        }
        assert UserFeedbackProcessor.validate(feedback, report_content) is None

    def test_validate_rejects_scope_encoded_supplementary_action(self):
        report_content = "原文"
        feedback = {
            "action": "supplementary_search_selected_and_related",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
        }
        with pytest.raises(CustomValueException) as exc_info:
            UserFeedbackProcessor.validate(feedback, report_content)

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code

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
                "original_text": "原文",
                "original_start_offset": 0,
                "original_end_offset": 2,
                "original_text_clean": "原文",
                "rewritten_text": "改写后的文本",
                "rewritten_start_offset": 0,
                "rewritten_end_offset": 6,
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
            "original_text": "原文",
            "original_start_offset": 0,
            "original_end_offset": 2,
            "original_text_clean": "原文",
            "rewritten_text": "改写后的文本",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 6,
        }
        mock_synonym_rewrite.assert_awaited_once_with(
            feedback=feedback,
            report_content="原文后续内容",
            language="zh-CN",
        )

    @pytest.mark.asyncio
    async def test_execute_applies_local_source_trace_for_rewrite_actions(self, processor):
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
        }
        final_result = {"response_content": "原文", "citation_messages": {"data": []}, "infer_messages": []}
        rewrite_result = {
            "new_report": "改写",
            "original_text": "原文",
            "original_start_offset": 0,
            "original_end_offset": 2,
            "original_text_clean": "原文",
            "rewritten_text": "改写",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 2,
        }
        traced_result = {**rewrite_result, "warning_info": "local trace degraded"}

        with patch.object(
            processor._synonym_rewriter,
            "synonym_rewrite",
            new_callable=AsyncMock,
            return_value=rewrite_result,
        ):
            with patch(
                "openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor."
                "apply_local_source_trace_to_action_result",
                new_callable=AsyncMock,
                return_value=traced_result,
            ) as mock_apply:
                result = await processor.execute(feedback=feedback, final_result=final_result, language="zh-CN")

        assert result["warning_info"] == "local trace degraded"
        mock_apply.assert_awaited_once_with(
            feedback=feedback,
            action_result=rewrite_result,
            final_result=final_result,
            llm_model_name=processor.llm_model_name,
            language="zh-CN",
        )

    @pytest.mark.asyncio
    async def test_execute_skips_local_source_trace_when_trace_source_switch_disabled(self, processor):
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
        }
        final_result = {"response_content": "原文", "citation_messages": {"data": []}, "infer_messages": []}
        rewrite_result = {
            "new_report": "改写",
            "original_text": "原文",
            "original_start_offset": 0,
            "original_end_offset": 2,
            "original_text_clean": "原文",
            "rewritten_text": "改写",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 2,
        }

        with patch.object(
            processor._synonym_rewriter,
            "synonym_rewrite",
            new_callable=AsyncMock,
            return_value=rewrite_result,
        ):
            with patch(
                "openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor."
                "apply_local_source_trace_to_action_result",
                new_callable=AsyncMock,
            ) as mock_apply:
                result = await processor.execute(
                    feedback=feedback,
                    final_result=final_result,
                    language="zh-CN",
                    enable_local_source_trace=False,
                )

        assert result == rewrite_result
        mock_apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_rejects_unsupported_action(self, processor):
        with pytest.raises(CustomValueException) as exc_info:
            await processor.execute(
                feedback={"action": "unsupported"},
                final_result={"response_content": "report"},
                language="zh-CN",
            )

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code

    @pytest.mark.asyncio
    async def test_execute_dispatches_supplementary_search_to_service(self, processor):
        feedback = {
            "action": "supplementary_search",
            "rewrite_scope": "selected_only",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
            "user_instruction": "补充这一段的信息",
        }
        final_result = {
            "response_content": "原文后续内容",
            "citation_messages": {},
            "infer_messages": [],
        }

        with patch.object(
            processor._supplementary_searcher,
            "supplementary_search",
            new_callable=AsyncMock,
        ) as mock_supplementary_search:
            mock_supplementary_search.return_value = {
                "new_report": "原文后续内容",
                "original_text": "原文",
                "original_start_offset": 0,
                "original_end_offset": 2,
                "original_text_clean": "原文",
                "rewritten_text": "## 第二章\n新章节内容",
                "rewritten_start_offset": 0,
                "rewritten_end_offset": 10,
            }

            result = await processor.execute(
                feedback=feedback,
                final_result=final_result,
                language="zh-CN",
            )

        mock_supplementary_search.assert_awaited_once_with(
            feedback=feedback,
            final_result=final_result,
            language="zh-CN",
        )

        assert result["rewritten_text"] == "## 第二章\n新章节内容"

    @pytest.mark.asyncio
    async def test_execute_dispatches_new_task_to_processor(self, processor):
        feedback = {
            "action": "new_task",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
            "user_instruction": "补充行业背景",
        }
        final_result = {
            "response_content": "原文后续内容",
            "citation_messages": {},
            "infer_messages": [],
        }

        with patch.object(
            processor._new_task_processor,
            "run_new_task",
            new_callable=AsyncMock,
        ) as mock_run_new_task:
            mock_run_new_task.return_value = {
                "new_report": "## 第一章\n新章节内容",
                "original_text": "## 第一章\n旧章节内容",
                "original_start_offset": 0,
                "original_end_offset": 11,
                "original_text_clean": "## 第一章\n旧章节内容",
                "rewritten_text": "## 第一章\n新章节内容",
                "rewritten_start_offset": 0,
                "rewritten_end_offset": 11,
                "section_start_offset": 0,
                "section_end_offset": 11,
                "section_title": "第一章",
                "matched_section_id": "1",
                "match_mode": "title_exact",
                "assessment_summary": "历史资料足够",
                "used_historical_doc_count": 2,
                "used_new_doc_count": 0,
                "missing_aspects": [],
            }

            result = await processor.execute(
                feedback=feedback,
                final_result=final_result,
                language="zh-CN",
            )

        assert result["section_title"] == "第一章"
        mock_run_new_task.assert_awaited_once_with(
            feedback=feedback,
            final_result=final_result,
            language="zh-CN",
        )

    @pytest.mark.asyncio
    async def test_execute_dispatches_truth_verification_to_processor(self, processor):
        feedback = {
            "action": "truth_verification",
            "selected_text": "结论段落\n更多内容",
            "start_offset": 0,
            "end_offset": 9,
            "user_instruction": "核验真实性",
        }
        final_result = {
            "response_content": "结论段落\n更多内容",
            "citation_messages": {},
            "infer_messages": [],
        }
        mock_current_report = MagicMock()

        with patch.object(
            processor._truth_verifier,
            "truth_verification",
            new_callable=AsyncMock,
        ) as mock_truth_verification:
            mock_truth_verification.return_value = {
                "read_only_result": True,
                "verification_result": {
                    "conclusion": "supported",
                    "summary": "核验通过",
                },
            }

            result = await processor.execute(
                feedback=feedback,
                final_result=final_result,
                language="zh-CN",
                current_report=mock_current_report,
            )

        assert result["read_only_result"] is True
        mock_truth_verification.assert_awaited_once_with(
            feedback=feedback,
            final_result=final_result,
            current_report=mock_current_report,
            language="zh-CN",
        )

    @pytest.mark.asyncio
    async def test_execute_sync_returns_updated_report_without_touching_metadata(self, processor):
        citation_messages = {"code": 0, "msg": "success", "data": [{"id": 0}]}
        infer_messages = [{"id": 9, "content": "保留"}]
        result = await processor.execute(
            feedback={"action": "sync", "selected_text": "用户改后的完整报告"},
            final_result={
                "response_content": "旧报告",
                "citation_messages": citation_messages,
                "infer_messages": infer_messages,
            },
            language="zh-CN",
        )

        assert result == {
            "sync_only": True,
            "new_report": "用户改后的完整报告",
            "original_text": "旧报告",
            "original_start_offset": 0,
            "original_end_offset": 3,
            "rewritten_text": "用户改后的完整报告",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 9,
        }

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

    def test_build_stream_result_returns_none_for_sync_action(self):
        assert UserFeedbackProcessor.build_stream_result(
            {"action": "sync", "selected_text": "完整报告"},
            {"sync_only": True, "new_report": "完整报告"},
        ) is None

    def test_build_stream_result_returns_truth_verification_payload(self):
        result = UserFeedbackProcessor.build_stream_result(
            {"action": "truth_verification", "selected_text": "段落"},
            {
                "read_only_result": True,
                "verification_result": {
                    "display_text": "核验通过",
                },
            },
        )
        assert result == "核验通过"

    def test_build_stream_result_builds_synonym_payload_from_action_result(self):
        feedback = {
            "action": "expand",
            "selected_text": "前端原文",
            "start_offset": 100,
            "end_offset": 104,
        }
        action_result = {
            "original_text": "执行结果原文",
            "original_start_offset": 3,
            "original_end_offset": 7,
            "rewritten_text": "执行结果改写",
            "rewritten_start_offset": 3,
            "rewritten_end_offset": 9,
        }

        result = UserFeedbackProcessor.build_stream_result(feedback, action_result)

        assert result == UserFeedbackRewriteStreamResult(
            original_text="执行结果原文",
            original_start_offset=3,
            original_end_offset=7,
            rewritten_text="执行结果改写",
            rewritten_start_offset=3,
            rewritten_end_offset=9,
            action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
            action_subcategory=SynonymRewriteActionSubcategory.EXPAND,
        )

    def test_build_stream_result_builds_local_edit_payload_for_supplementary_search(self):
        feedback = {
            "action": "supplementary_search",
            "rewrite_scope": "selected_only",
            "selected_text": "原文",
            "start_offset": 3,
            "end_offset": 5,
        }
        action_result = {
            "original_text": "## 第二章\n旧章节内容",
            "original_start_offset": 0,
            "original_end_offset": 11,
            "rewritten_text": "## 第二章\n新章节内容",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 11,
        }

        result = UserFeedbackProcessor.build_stream_result(feedback, action_result)

        assert result == UserFeedbackRewriteStreamResult(
            original_text="## 第二章\n旧章节内容",
            original_start_offset=0,
            original_end_offset=11,
            rewritten_text="## 第二章\n新章节内容",
            rewritten_start_offset=0,
            rewritten_end_offset=11,
            action_category=UserFeedbackActionCategory.SUPPLEMENTARY_SEARCH,
            action_subcategory=SupplementarySearchActionSubcategory.SUPPLEMENTARY_SEARCH,
        )

    def test_build_stream_result_builds_local_edit_payload_for_new_task(self):
        feedback = {
            "action": "new_task",
            "selected_text": "原文",
            "start_offset": 3,
            "end_offset": 5,
        }
        action_result = {
            "original_text": "## 第一章\n旧章节内容",
            "original_start_offset": 0,
            "original_end_offset": 11,
            "rewritten_text": "## 第一章\n新章节内容",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 11,
        }

        result = UserFeedbackProcessor.build_stream_result(feedback, action_result)

        assert result.action_category == UserFeedbackActionCategory.NEW_TASK
        assert result.rewritten_text == "## 第一章\n新章节内容"

    def test_build_stream_result_uses_rewrite_error_errmsg_for_invalid_rewrite_mapping(self):
        feedback = {
            "action": "expand",
            "selected_text": "原文",
            "start_offset": 0,
            "end_offset": 2,
        }
        action_result = {
            "original_text": "原文",
            "original_start_offset": 0,
            "original_end_offset": 2,
            "rewritten_text": "改写",
            "rewritten_start_offset": 0,
            "rewritten_end_offset": 2,
        }

        with patch(
            "openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor.resolve_feedback_action",
            return_value=ResolvedUserAction(
                action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
                action_subcategory=SupplementarySearchActionSubcategory.SUPPLEMENTARY_SEARCH,
            ),
        ):
            with pytest.raises(CustomRuntimeException) as exc_info:
                UserFeedbackProcessor.build_stream_result(feedback, action_result)

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
        assert exc_info.value.message == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(
            e="Rewrite stream result requires synonym_rewrite subcategory, got supplementary_search"
        )

    def test_build_stream_result_raises_when_truth_result_is_not_dict(self):
        with pytest.raises(CustomRuntimeException) as exc_info:
            UserFeedbackProcessor.build_stream_result(
                {"action": "truth_verification", "selected_text": "段落"},
                {"read_only_result": True, "verification_result": "invalid"},
            )

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code

    def test_build_stream_result_raises_when_truth_display_text_is_empty(self):
        with pytest.raises(CustomRuntimeException) as exc_info:
            UserFeedbackProcessor.build_stream_result(
                {"action": "truth_verification", "selected_text": "段落"},
                {"read_only_result": True, "verification_result": {"display_text": ""}},
            )

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code


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
            "feedback_interaction_count": 0,
            "final_result": {
                "response_content": "完整改写后的报告",
                "citation_messages": {"code": 0, "msg": "success", "data": []},
                "infer_messages": [{"id": 0, "content": "保留推理"}],
                "exception_info": "[212405] stale error",
                "warning_info": "ignored",
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

    @pytest.mark.asyncio
    async def test_send_result_outputs_supplementary_search_metadata(self):
        session = MagicMock()
        session.write_custom_stream = AsyncMock()

        feedback = {"action": "supplementary_search"}
        result = UserFeedbackRewriteStreamResult(
            original_text="原始文本",
            original_start_offset=0,
            original_end_offset=4,
            rewritten_text="## 第二章\n新章节内容",
            rewritten_start_offset=0,
            rewritten_end_offset=11,
            action_category=UserFeedbackActionCategory.SUPPLEMENTARY_SEARCH,
            action_subcategory=SupplementarySearchActionSubcategory.SUPPLEMENTARY_SEARCH,
        )

        await UserFeedbackProcessor.send_result(
            session=session,
            feedback=feedback,
            result=result,
            final_result={
                "response_content": "新报告",
                "citation_messages": {"data": []},
                "infer_messages": [],
            },
        )

        payload = session.write_custom_stream.await_args.args[0]
        content = json.loads(payload["content"])
        assert content["action_category"] == "supplementary_search"
        assert content["action_subcategory"] == "supplementary_search"
        assert content["final_result"]["response_content"] == "新报告"

    @pytest.mark.asyncio
    async def test_send_result_outputs_lightweight_sync_ack(self):
        session = MagicMock()
        session.write_custom_stream = AsyncMock()

        await UserFeedbackProcessor.send_result(
            session=session,
            feedback={"action": "sync", "selected_text": "完整报告"},
            result=None,
        )

        payload = session.write_custom_stream.await_args.args[0]
        assert json.loads(payload["content"]) == {
            "action_category": "sync",
            "synced": True,
        }

    @pytest.mark.asyncio
    async def test_send_result_outputs_truth_verification_payload(self):
        session = MagicMock()
        session.write_custom_stream = AsyncMock()

        await UserFeedbackProcessor.send_result(
            session=session,
            feedback={"action": "truth_verification", "selected_text": "段落"},
            result="部分支持",
            final_result={"response_content": "旧报告"},
            feedback_interaction_count=2,
        )

        payload = session.write_custom_stream.await_args.args[0]
        assert json.loads(payload["content"]) == {
            "display_text": "部分支持",
            "feedback_interaction_count": 2,
        }
        assert payload["event"] == StreamEvent.SUMMARY_RESPONSE.value


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
