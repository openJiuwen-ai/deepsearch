# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import uuid

from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import (
    SynonymRewriteActionSubcategory,
    SYNONYM_REWRITE_ACTIONS,
    UserFeedbackActionCategory,
    UserFeedbackRewriteStreamResult,
    resolve_user_input_action,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.synonym_rewrite import SynonymRewriter
from openjiuwen_deepsearch.common.exception import (
    CustomException,
    CustomRuntimeException,
    CustomValueException,
)
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.stream_utils import (
    get_current_time, MessageType, StreamEvent)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)

_ALL_VALID_ACTIONS = SYNONYM_REWRITE_ACTIONS | {"finish"}


class UserFeedbackProcessor:
    """处理用户对报告局部内容的改写反馈。

    职责分为三类：
    1. 解析并校验前端传入的反馈参数。
    2. 执行改写，并同步维护报告中的引用元数据。
    3. 将成功结果或错误信息以流式消息发送给前端。
    """

    def __init__(self, llm_model_name: str):
        self.llm_model_name = llm_model_name
        # 同义改写处理器，目前只支持同义改写
        self._synonym_rewriter = SynonymRewriter(llm_model_name)

    # ------------------------------------------------------------------
    # 解析 & 校验
    # ------------------------------------------------------------------

    @staticmethod
    def parse_feedback(raw_input: str) -> dict:
        """解析前端 JSON 输入。"""
        try:
            data = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError) as error:
            msg = StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.errmsg.format(e=str(error))
            logger.error(f"[UserFeedbackProcessor] {msg}")
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.code,
                msg,
            ) from error

        if not isinstance(data, dict):
            msg = StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.errmsg.format(
                e=f"expected JSON object, got {type(data).__name__}"
            )
            logger.error(f"[UserFeedbackProcessor] {msg}")
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON.code,
                msg,
            )

        action = data.get("action")
        if not isinstance(action, str) or not action:
            msg = StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action)
            logger.error(f"[UserFeedbackProcessor] {msg}")
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
                msg,
            )

        return data

    @staticmethod
    def validate(feedback: dict, report_content: str, max_text_length: int) -> None:
        """校验反馈输入。"""
        action = feedback.get("action", "")
        if not isinstance(action, str) or action not in _ALL_VALID_ACTIONS:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action),
            )

        if action == "finish":
            return None

        selected_text = feedback.get("selected_text", "")
        start_offset = feedback.get("start_offset", 0)
        end_offset = feedback.get("end_offset", 0)

        if not isinstance(selected_text, str):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="selected_text",
                    expected_type="str",
                ),
            )
        if not isinstance(start_offset, int):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="start_offset",
                    expected_type="int",
                ),
            )
        if not isinstance(end_offset, int):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="end_offset",
                    expected_type="int",
                ),
            )

        if len(selected_text) > max_text_length:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_TEXT_TOO_LONG.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_TEXT_TOO_LONG.errmsg.format(max_length=max_text_length),
            )

        if start_offset < 0 or end_offset > len(report_content) or start_offset >= end_offset:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_OFFSET_RANGE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_OFFSET_RANGE.errmsg.format(
                    start=start_offset,
                    end=end_offset,
                ),
            )

        if report_content[start_offset:end_offset] != selected_text:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_OFFSET_MISMATCH.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_OFFSET_MISMATCH.errmsg.format(
                    start=start_offset,
                    end=end_offset,
                ),
            )

        return None

    # ------------------------------------------------------------------
    # 执行反馈动作
    # ------------------------------------------------------------------

    async def execute(
        self,
        feedback: dict,
        final_result,
        language: str,
    ) -> dict:
        """统一入口：根据 action 类型路由到具体处理逻辑。

        Returns:
            dict: 改写后的结果快照，包含：
                - new_report: 更新后的完整报告
                - rewritten_text: LLM 返回的改写文本
                - start_offset / new_end_offset: 改写后文本在新报告中的区间
                - updated_citation_messages: 同步更新后的引用信息
                - updated_infer_messages: 过滤后的推理消息列表
        """
        action = feedback["action"]

        report_content = final_result.get("response_content", "") or ""
        citation_messages = dict(final_result.get("citation_messages", {}) or {})
        infer_messages = list(final_result.get("infer_messages", []) or [])

        if action in SYNONYM_REWRITE_ACTIONS:
            rewrite_result = await self._synonym_rewriter.synonym_rewrite(
                feedback=feedback,
                report_content=report_content,
                citation_messages=citation_messages,
                language=language,
                infer_messages=infer_messages,
            )
            return dict(
                new_report=rewrite_result["new_report"],
                original_text_clean=rewrite_result.get("original_text_clean", feedback.get("selected_text", "")),
                rewritten_text=rewrite_result["rewritten_text"],
                start_offset=rewrite_result["start_offset"],
                new_end_offset=rewrite_result["new_end_offset"],
                updated_citation_messages=rewrite_result["updated_messages"],
                updated_infer_messages=rewrite_result["updated_infer_messages"],
            )

        # 后续根据不同的action，调用不同的处理逻辑

        raise CustomValueException(
            StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
            StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action),
        )

    # ------------------------------------------------------------------
    # 流式输出
    # ------------------------------------------------------------------

    @staticmethod
    def build_stream_result(feedback: dict, action_result: dict) -> object | None:
        mapping = resolve_user_input_action(feedback["action"])
        builders = {
            UserFeedbackActionCategory.SYNONYM_REWRITE:
                UserFeedbackProcessor._build_synonym_rewrite_stream_result,
            UserFeedbackActionCategory.SUPPLEMENTARY_SEARCH:
                UserFeedbackProcessor._build_supplementary_search_stream_result,
            UserFeedbackActionCategory.NEW_TASK:
                UserFeedbackProcessor._build_new_task_stream_result,
            UserFeedbackActionCategory.SECTION_CHANGE:
                UserFeedbackProcessor._build_section_change_stream_result,
            UserFeedbackActionCategory.FINISH:
                UserFeedbackProcessor._build_finish_stream_result,
        }
        builder = builders.get(mapping.action_category)
        if builder is None:
            UserFeedbackProcessor._raise_stream_result_error(
                f"Unsupported action_category for stream result: {mapping.action_category}"
            )
        return builder(feedback, action_result, mapping)

    @staticmethod
    def _build_synonym_rewrite_stream_result(
        feedback: dict,
        action_result: dict,
        mapping,
    ) -> UserFeedbackRewriteStreamResult:
        subcategory = mapping.action_subcategory
        if not isinstance(subcategory, SynonymRewriteActionSubcategory):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Rewrite stream result requires synonym_rewrite subcategory, got action: {feedback['action']}"
            )
        return UserFeedbackRewriteStreamResult(
            original_text=feedback["selected_text"],
            original_start_offset=feedback["start_offset"],
            original_end_offset=feedback["end_offset"],
            rewritten_text=action_result["rewritten_text"],
            rewritten_start_offset=action_result["start_offset"],
            rewritten_end_offset=action_result["new_end_offset"],
            action_category=mapping.action_category,
            action_subcategory=subcategory,
        )

    @staticmethod
    def _build_supplementary_search_stream_result(feedback: dict, action_result: dict, mapping) -> None:
        return None

    @staticmethod
    def _build_new_task_stream_result(feedback: dict, action_result: dict, mapping) -> None:
        return None

    @staticmethod
    def _build_section_change_stream_result(feedback: dict, action_result: dict, mapping) -> None:
        return None

    @staticmethod
    def _build_finish_stream_result(feedback: dict, action_result: dict, mapping) -> None:
        return None

    @staticmethod
    def _raise_stream_result_error(message: str) -> None:
        raise CustomRuntimeException(
            StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
            StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=message),
        )

    @staticmethod
    async def send_result(session, feedback: dict, result: object | None, final_result: dict | None = None):
        mapping = resolve_user_input_action(feedback["action"])
        senders = {
            UserFeedbackActionCategory.SYNONYM_REWRITE:
                UserFeedbackProcessor._send_synonym_rewrite_result,
            UserFeedbackActionCategory.SUPPLEMENTARY_SEARCH:
                UserFeedbackProcessor._send_supplementary_search_result,
            UserFeedbackActionCategory.NEW_TASK:
                UserFeedbackProcessor._send_new_task_result,
            UserFeedbackActionCategory.SECTION_CHANGE:
                UserFeedbackProcessor._send_section_change_result,
            UserFeedbackActionCategory.FINISH:
                UserFeedbackProcessor._send_finish_result,
        }
        sender = senders.get(mapping.action_category)
        if sender is None:
            UserFeedbackProcessor._raise_stream_result_error(
                f"Unsupported action_category for send_result: {mapping.action_category}"
            )
        await sender(session, result, final_result)

    @staticmethod
    async def _send_synonym_rewrite_result(session, result: object | None, final_result: dict | None = None):
        """向前端发送改写成功结果。

        先返回局部变更信息，便于前端按区间替换现有内容，
        再同步返回最新的完整 final_result 快照。
        """
        if not isinstance(result, UserFeedbackRewriteStreamResult):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Expected UserFeedbackRewriteStreamResult, got {type(result).__name__}"
            )
        content_payload = {
            "original_text": result.original_text,
            "original_start_offset": result.original_start_offset,
            "original_end_offset": result.original_end_offset,
            "rewritten_text": result.rewritten_text,
            "rewritten_start_offset": result.rewritten_start_offset,
            "rewritten_end_offset": result.rewritten_end_offset,
            "action_category": result.action_category.value,
            "action_subcategory": result.action_subcategory.value,
        }
        if final_result is not None:
            content_payload["final_result"] = {
                "response_content": final_result.get("response_content", ""),
                "citation_messages": final_result.get("citation_messages", {}),
                "infer_messages": final_result.get("infer_messages", []),
            }

        content = json.dumps(content_payload, ensure_ascii=False)
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.SUMMARY_RESPONSE.value,
            "created_time": get_current_time(),
        })

    @staticmethod
    async def _send_supplementary_search_result(session, result: object | None, final_result: dict | None = None):
        return None

    @staticmethod
    async def _send_new_task_result(session, result: object | None, final_result: dict | None = None):
        return None

    @staticmethod
    async def _send_section_change_result(session, result: object | None, final_result: dict | None = None):
        return None

    @staticmethod
    async def _send_finish_result(session, result: object | None, final_result: dict | None = None):
        return None

    @staticmethod
    async def send_error(session, error_msg: str | Exception):
        """向前端发送错误信息。"""
        content = json.dumps({"error": UserFeedbackProcessor._stringify_error(error_msg)}, ensure_ascii=False)
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.ERROR.value,
            "created_time": get_current_time(),
        })

    # ------------------------------------------------------------------
    # 错误处理
    # ------------------------------------------------------------------

    @staticmethod
    def _stringify_error(error: str | Exception) -> str:
        if isinstance(error, CustomException):
            return str(error)
        if isinstance(error, Exception):
            wrapped_error = CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=str(error)),
            )
            return str(wrapped_error)
        return str(error)
