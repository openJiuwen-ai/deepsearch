# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import uuid

from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import (
    NewTaskActionSubcategory,
    ResolvedUserAction,
    SupplementarySearchActionSubcategory,
    SyncActionSubcategory,
    SynonymRewriteActionSubcategory,
    TruthVerificationActionSubcategory,
    SYNONYM_REWRITE_ACTIONS,
    USER_INPUT_ACTION_MAP,
    UserFeedbackActionCategory,
    UserFeedbackRewriteStreamResult,
    resolve_feedback_action,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.supplementary_search import (
    SupplementarySearcher,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace import (
    apply_local_source_trace_to_action_result,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.synonym_rewrite import SynonymRewriter
from openjiuwen_deepsearch.algorithm.user_feedback_processor.new_task_processor import NewTaskProcessor
from openjiuwen_deepsearch.algorithm.user_feedback_processor.truth_verification import TruthVerificationProcessor
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
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

_ALL_VALID_ACTIONS = frozenset(USER_INPUT_ACTION_MAP.keys())
_SUPPLEMENTARY_SEARCH_REWRITE_SCOPES = frozenset({"selected_only", "selected_and_related"})


class UserFeedbackProcessor:
    """处理用户对报告局部内容的改写反馈。

    职责分为三类：
    1. 解析并校验前端传入的反馈参数。
    2. 执行改写，并同步维护报告中的引用元数据。
    3. 将成功结果或错误信息以流式消息发送给前端。
    """

    def __init__(self, llm_model_name: str):
        self.llm_model_name = llm_model_name
        # 同义改写处理器
        self._synonym_rewriter = SynonymRewriter(llm_model_name)
        # 补充搜索处理器
        self._supplementary_searcher = SupplementarySearcher(llm_model_name)
        # 新增任务处理器
        self._new_task_processor = NewTaskProcessor(llm_model_name)
        # 内容真实性核验处理器
        self._truth_verifier = TruthVerificationProcessor(llm_model_name)

    # ------------------------------------------------------------------
    # 解析 & 校验
    # ------------------------------------------------------------------
    # parse_feedback：wire 格式（字符串 → dict）及与报告无关的最浅约束。
    # validate：在报告正文上下文中做类型、选区一致性、动作白名单等语义校验。

    @staticmethod
    def parse_feedback(raw_input: str) -> dict:
        """解析前端传入的 JSON 反馈字符串。

        该阶段只负责把json字符串转换为字典，并补齐协议中的默认字段；
        更完整的动作白名单校验、选区偏移校验和语义校验由 ``validate`` 负责。

        Args:
            raw_input: 前端传入的原始 JSON 字符串。

        Returns:
            dict: 解析后的反馈字典；当 ``rewrite_scope`` 缺失或为空时，会补为
                ``selected_only``。

        Raises:
            CustomValueException: 当输入不是合法 JSON 对象，或 ``action`` 缺失、
                为空、类型不合法时抛出。
        """
        try:
            data = json.loads(raw_input)
            logger.info(
                f"[UserFeedbackProcessor] parse_feedback: data="
                f"{'*' if LogManager.is_sensitive() else json.dumps(data, ensure_ascii=False, indent=4)}"
            )
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

        if "action" not in data or data.get("action") is None:
            action = data.get("action")
            msg = StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action)
            logger.error(f"[UserFeedbackProcessor] {msg}")
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
                msg,
            )

        action = data["action"]
        if not isinstance(action, str) or not action:
            msg = StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action)
            logger.error(f"[UserFeedbackProcessor] {msg}")
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
                msg,
            )

        rewrite_scope = data.get("rewrite_scope")
        if rewrite_scope is None or rewrite_scope == "":
            data["rewrite_scope"] = "selected_only"

        return data

    @staticmethod
    def validate(
        feedback: dict,
        report_content: str,
    ) -> None:
        """对反馈字典做语义校验。

        Args:
            feedback: 用户反馈字典。
            report_content: 当前报告正文。

        Raises:
            CustomValueException: 当校验失败时抛出。
        """
        UserFeedbackProcessor.validate_basic(feedback, report_content)
        UserFeedbackProcessor.validate_by_action(feedback)

    @staticmethod
    def validate_basic(
        feedback: dict,
        report_content: str,
    ) -> None:
        """执行基础字段校验，并按动作决定是否校验选区偏移。

        Args:
            feedback: 用户反馈字典。
            report_content: 当前报告正文。

        Returns:
            None

        Raises:
            CustomValueException: 当字段类型、偏移范围或选区内容不合法时抛出。
        """
        action = feedback.get("action", "")
        selected_text = feedback.get("selected_text", "")
        start_offset = feedback.get("start_offset", 0)
        end_offset = feedback.get("end_offset", 0)

        logger.info(f"[UserFeedbackProcessor] validate_basic: action={action}")
        logger.info(f"[UserFeedbackProcessor] validate_basic: "
        f"selected_text={'*' if LogManager.is_sensitive() else selected_text}"
        f"start_offset={'*' if LogManager.is_sensitive() else start_offset}"
        f"end_offset={'*' if LogManager.is_sensitive() else end_offset}")

        if "user_instruction" in feedback and not isinstance(feedback.get("user_instruction"), str):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="user_instruction",
                    expected_type="str",
                ),
            )
        if "rewrite_scope" in feedback and not isinstance(feedback.get("rewrite_scope"), str):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="rewrite_scope",
                    expected_type="str",
                ),
            )

        if action == "finish":
            return None

        # sync 依赖 selected_text 承载整篇报告内容，缺字段时必须在校验阶段拦截。
        if action == "sync" and "selected_text" not in feedback:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="selected_text",
                    expected_type="str",
                ),
            )

        if not isinstance(selected_text, str):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="selected_text",
                    expected_type="str",
                ),
            )
        if action == "sync" and selected_text == "":
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="selected_text",
                    expected_type="non-empty str",
                ),
            )
        # sync 表示前端已给出整篇新报告，此时不再要求局部 offset 与正文逐字对齐。
        if action == "sync":
            return None

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

    @staticmethod
    def validate_by_action(feedback: dict) -> None:
        """按动作类型执行白名单与动作专属字段校验。

        当前动作级校验主要包括两部分：一是 ``action`` 必须属于协议白名单；
        二是补充搜索动作的 ``rewrite_scope`` 必须落在支持范围内。更基础的字段
        类型和 offset 校验由 ``validate_basic`` 负责。

        Args:
            feedback: 已解析的用户反馈字典。

        Returns:
            None

        Raises:
            CustomValueException: 当动作不在白名单内，或动作专属字段不合法时抛出。
        """
        action = feedback.get("action", "")
        if not isinstance(action, str):
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="action",
                    expected_type="str",
                ),
            )
        if action not in _ALL_VALID_ACTIONS:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action),
            )
        if action == "supplementary_search":
            # rewrite_scope 缺省由 parse_feedback 填充；非 str 由 validate_basic 拦截。
            rewrite_scope = feedback.get("rewrite_scope")
            if rewrite_scope not in _SUPPLEMENTARY_SEARCH_REWRITE_SCOPES:
                raise CustomValueException(
                    StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_REWRITE_SCOPE.code,
                    StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_REWRITE_SCOPE.errmsg.format(
                        rewrite_scope=rewrite_scope,
                    ),
                )

    # ------------------------------------------------------------------
    # 流式输出
    # ------------------------------------------------------------------

    @staticmethod
    def build_stream_result(feedback: dict, action_result: dict) -> object | None:
        """将执行结果转换为前端流式输出所需的结构。

        Args:
            feedback: 已解析的用户反馈字典。
            action_result: ``execute`` 返回的原始结果。

        Returns:
            object | None: 改写动作返回 ``UserFeedbackRewriteStreamResult``，
                非改写动作返回 ``None``。
        """
        resolved_action = resolve_feedback_action(feedback)
        builder, _ = UserFeedbackProcessor._resolve_action_runtime_hooks(
            resolved_action.action_category,
            usage="stream result",
        )
        return builder(action_result, resolved_action)

    @staticmethod
    async def send_result(
        session,
        feedback: dict,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """按动作大类将结果分发到对应的流式发送函数。

        Args:
            session: 当前会话对象。
            feedback: 已解析的用户反馈字典。
            result: 由 ``build_stream_result`` 生成的结构化结果。
            final_result: 可选的完整结果快照。
            feedback_interaction_count: 当前反馈交互计数。

        Returns:
            None
        """
        resolved_action = resolve_feedback_action(feedback)
        _, sender = UserFeedbackProcessor._resolve_action_runtime_hooks(
            resolved_action.action_category,
            usage="send_result",
        )
        await sender(session, result, final_result, feedback_interaction_count)

    @staticmethod
    async def send_error(session, error_msg: str | Exception):
        """向前端发送统一格式的错误消息。

        Args:
            session: 当前会话对象。
            error_msg: 字符串错误信息或异常对象。

        Returns:
            None
        """
        content = json.dumps({"error": UserFeedbackProcessor._stringify_error(error_msg)}, ensure_ascii=False)
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.ERROR.value,
            "created_time": get_current_time(),
        })

    @staticmethod
    def _resolve_action_runtime_hooks(
        action_category: UserFeedbackActionCategory,
        usage: str,
    ) -> tuple:
        """根据动作大类解析流式构建与发送函数。

        Args:
            action_category: 反馈动作大类。
            usage: 当前用途描述，仅用于拼装异常信息。

        Returns:
            tuple: ``(stream_result_builder, send_result_fn)`` 二元组。

        Raises:
            CustomRuntimeException: 当动作大类没有注册对应运行时钩子时抛出。
        """
        hooks = {
            UserFeedbackActionCategory.SYNONYM_REWRITE: (
                UserFeedbackProcessor._build_synonym_rewrite_stream_result,
                UserFeedbackProcessor._send_synonym_rewrite_result,
            ),
            UserFeedbackActionCategory.SUPPLEMENTARY_SEARCH: (
                UserFeedbackProcessor._build_supplementary_search_stream_result,
                UserFeedbackProcessor._send_supplementary_search_result,
            ),
            UserFeedbackActionCategory.NEW_TASK: (
                UserFeedbackProcessor._build_new_task_stream_result,
                UserFeedbackProcessor._send_new_task_result,
            ),
            UserFeedbackActionCategory.TRUTH_VERIFICATION: (
                UserFeedbackProcessor._build_truth_verification_stream_result,
                UserFeedbackProcessor._send_truth_verification_result,
            ),
            UserFeedbackActionCategory.SECTION_CHANGE: (
                UserFeedbackProcessor._build_section_change_stream_result,
                UserFeedbackProcessor._send_section_change_result,
            ),
            UserFeedbackActionCategory.SYNC: (
                UserFeedbackProcessor._build_sync_stream_result,
                UserFeedbackProcessor._send_sync_result,
            ),
            UserFeedbackActionCategory.FINISH: (
                UserFeedbackProcessor._build_finish_stream_result,
                UserFeedbackProcessor._send_finish_result,
            ),
        }
        runtime_hooks = hooks.get(action_category)
        if runtime_hooks is None:
            UserFeedbackProcessor._raise_stream_result_error(
                f"Unsupported action_category for {usage}: {action_category}"
            )
        return runtime_hooks

    @staticmethod
    def _build_rewrite_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> UserFeedbackRewriteStreamResult:
        """从 ``action_result`` 抽取偏移与文本，组装 ``UserFeedbackRewriteStreamResult``。

        Args:
            action_result: execute 方法返回的原始结果字典。
            resolved_action: 解析后的动作对象。

        Returns:
            UserFeedbackRewriteStreamResult: 流式改写结果对象。
        """
        return UserFeedbackRewriteStreamResult(
            original_text=action_result["original_text"],
            original_start_offset=action_result["original_start_offset"],
            original_end_offset=action_result["original_end_offset"],
            rewritten_text=action_result["rewritten_text"],
            rewritten_start_offset=action_result["rewritten_start_offset"],
            rewritten_end_offset=action_result["rewritten_end_offset"],
            action_category=resolved_action.action_category,
            action_subcategory=resolved_action.action_subcategory,
        )

    @staticmethod
    def _build_synonym_rewrite_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> UserFeedbackRewriteStreamResult:
        """校验小类为同义改写后，委托 ``_build_rewrite_stream_result``。

        Args:
            action_result: execute 方法返回的原始结果字典。
            resolved_action: 解析后的动作对象。

        Returns:
            UserFeedbackRewriteStreamResult: 流式改写结果对象。

        Raises:
            CustomRuntimeException: 当动作小类不是同义改写时抛出。
        """
        subcategory = resolved_action.action_subcategory
        if not isinstance(subcategory, SynonymRewriteActionSubcategory):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Rewrite stream result requires synonym_rewrite subcategory, got {subcategory.value}"
            )
        return UserFeedbackProcessor._build_rewrite_stream_result(action_result, resolved_action)

    @staticmethod
    def _build_supplementary_search_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> UserFeedbackRewriteStreamResult:
        """构建补充搜索动作的流式改写结果。

        Args:
            action_result: execute 方法返回的原始结果字典。
            resolved_action: 解析后的动作对象。

        Returns:
            UserFeedbackRewriteStreamResult: 流式改写结果对象。

        Raises:
            CustomRuntimeException: 当动作小类不是补充搜索时抛出。
        """
        subcategory = resolved_action.action_subcategory
        if not isinstance(subcategory, SupplementarySearchActionSubcategory):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Rewrite stream result requires supplementary_search subcategory, got {subcategory.value}"
            )
        return UserFeedbackProcessor._build_rewrite_stream_result(action_result, resolved_action)

    @staticmethod
    def _build_new_task_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> UserFeedbackRewriteStreamResult:
        """构建 ``new_task`` 动作的流式改写结果。"""
        subcategory = resolved_action.action_subcategory
        if not isinstance(subcategory, NewTaskActionSubcategory):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Rewrite stream result requires new_task subcategory, got {subcategory.value}"
            )
        return UserFeedbackProcessor._build_rewrite_stream_result(action_result, resolved_action)

    @staticmethod
    def _build_truth_verification_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> str:
        """构建内容真实性核验动作的流式结果。"""
        subcategory = resolved_action.action_subcategory
        if not isinstance(subcategory, TruthVerificationActionSubcategory):
            UserFeedbackProcessor._raise_stream_result_error(
                "Truth verification stream result requires truth_verification subcategory."
            )
        verification_result = action_result.get("verification_result")
        if not isinstance(verification_result, dict):
            UserFeedbackProcessor._raise_stream_result_error(
                "Truth verification stream result requires dict verification_result."
            )
        display_text = verification_result.get("display_text", "")
        if not isinstance(display_text, str) or not display_text.strip():
            UserFeedbackProcessor._raise_stream_result_error(
                "Truth verification stream result requires non-empty display_text."
            )
        return display_text.strip()

    @staticmethod
    def _build_sync_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> None:
        """构建 sync 流式结果。

        Args:
            action_result: execute 方法返回的原始结果字典。
            resolved_action: 解析后的动作对象。

        Returns:
            None: sync 只发送轻量确认消息，不下发局部替换结构。

        Raises:
            CustomRuntimeException: 当动作小类不是 sync 时抛出。
        """
        subcategory = resolved_action.action_subcategory
        if not isinstance(subcategory, SyncActionSubcategory):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Sync stream result requires sync subcategory, got {subcategory.value}"
            )

    @staticmethod
    def _build_section_change_stream_result(
        action_result: dict,
        resolved_action: ResolvedUserAction,
    ) -> None:
        return None

    @staticmethod
    def _build_finish_stream_result(action_result: dict, resolved_action: ResolvedUserAction) -> None:
        return None

    @staticmethod
    async def _send_rewrite_stream_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """向前端发送结构化改写结果。

        先返回局部变更信息，便于前端按区间替换现有内容，
        再同步返回最新的完整 ``final_result`` 快照。

        Args:
            session: 当前会话对象。
            result: 结构化改写结果对象，必须为 ``UserFeedbackRewriteStreamResult``。
            final_result: 可选的完整结果快照。
            feedback_interaction_count: 当前反馈交互计数。

        Returns:
            None

        Raises:
            CustomRuntimeException: 当 ``result`` 类型不符合预期时抛出。
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
            "feedback_interaction_count": feedback_interaction_count,
        }
        if final_result is not None:
            content_payload["final_result"] = final_result

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
    async def _send_synonym_rewrite_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """发送同义改写动作的流式结果。

        Args:
            session: 当前会话对象。
            result: 流式改写结果对象。
            final_result: 可选的完整结果快照。
            feedback_interaction_count: 当前反馈交互计数。

        Returns:
            None
        """
        await UserFeedbackProcessor._send_rewrite_stream_result(
            session, result, final_result, feedback_interaction_count
        )

    @staticmethod
    async def _send_supplementary_search_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """补充搜索：复用 ``_send_rewrite_stream_result``。

        Args:
            session: 当前会话对象。
            result: 流式结果对象。
            final_result: 可选的完整结果快照。
            feedback_interaction_count: 当前反馈交互计数。
        """
        await UserFeedbackProcessor._send_rewrite_stream_result(
            session, result, final_result, feedback_interaction_count
        )

    @staticmethod
    async def _send_new_task_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """new_task 复用通用改写发送逻辑。"""
        await UserFeedbackProcessor._send_rewrite_stream_result(
            session,
            result,
            final_result,
            feedback_interaction_count,
        )

    @staticmethod
    async def _send_truth_verification_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """向前端发送内容真实性核验结果。"""
        if not isinstance(result, str):
            UserFeedbackProcessor._raise_stream_result_error(
                f"Expected truth verification display text, got {type(result).__name__}"
            )
        display_text = result.strip()
        if not display_text:
            UserFeedbackProcessor._raise_stream_result_error(
                "Truth verification display text cannot be empty."
            )
        content = json.dumps(
            {
                "display_text": display_text,
                "feedback_interaction_count": feedback_interaction_count,
            },
            ensure_ascii=False,
        )
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.SUMMARY_RESPONSE.value,
            "created_time": get_current_time(),
        })

    @staticmethod
    async def _send_sync_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        """向前端发送整篇同步成功的轻量确认消息。

        Args:
            session: 当前会话对象。
            result: sync 流程不使用该字段，保留以兼容统一 sender 签名。
            final_result: sync 流程不使用该字段，保留以兼容统一 sender 签名。
            feedback_interaction_count: sync 流程不使用该字段，保留以兼容统一 sender 签名。

        Returns:
            None
        """
        content = json.dumps(
            {"action_category": UserFeedbackActionCategory.SYNC.value, "synced": True},
            ensure_ascii=False,
        )
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.SUMMARY_RESPONSE.value,
            "created_time": get_current_time(),
        })

    @staticmethod
    async def _send_section_change_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        return None

    @staticmethod
    async def _send_finish_result(
        session,
        result: object | None,
        final_result: dict | None = None,
        feedback_interaction_count: int = 0,
    ):
        return None

    # ------------------------------------------------------------------
    # 错误处理
    # ------------------------------------------------------------------

    @staticmethod
    def _stringify_error(error: str | Exception) -> str:
        """将错误信息转换为字符串格式。

        Args:
            error: 错误消息字符串或异常对象。

        Returns:
            str: 格式化后的错误消息字符串。
        """
        if isinstance(error, CustomException):
            return str(error)
        if isinstance(error, Exception):
            wrapped_error = CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=str(error)),
            )
            return str(wrapped_error)
        return str(error)

    @staticmethod
    def _raise_stream_result_error(message: str) -> None:
        raise CustomRuntimeException(
            StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
            StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=message),
        )

    # ------------------------------------------------------------------
    # 执行反馈动作
    # ------------------------------------------------------------------

    async def execute(
        self,
        feedback: dict,
        final_result,
        language: str,
        enable_local_source_trace: bool = True,
        current_report=None,
    ) -> dict:
        """根据动作类型路由到具体反馈处理逻辑。

        ``sync`` 动作会直接接收前端给出的整篇新报告并沿用当前 metadata；
        同义改写与补充搜索则会进入对应子处理器，返回统一结构的结果快照，
        供节点层写回 session 并下发流式结果。

        Args:
            feedback: 已通过校验的用户反馈字典。
            final_result: 当前报告完整结果，至少包含正文与 metadata。
            language: 当前报告语言。
            enable_local_source_trace: 是否在改写后执行差异感知局部溯源。

        Returns:
            dict: 改写后的结果快照，包含：
                - new_report: 更新后的完整报告
                - original_text / original_start_offset / original_end_offset: 原始被替换区间
                - original_text_clean: 剥离选区内标记后的原文片段
                - rewritten_text: LLM 返回的改写文本
                - rewritten_start_offset / rewritten_end_offset: 改写后文本在新报告中的区间
                - section_start_offset / section_end_offset / collector_summary: 补充搜索路径附加字段（可选）
        """
        action = feedback["action"]

        report_content = final_result.get("response_content", "") or ""
        action_mapping = USER_INPUT_ACTION_MAP.get(action)
        flow_name = action_mapping.action_category.value if action_mapping else "unsupported"
        logger.info(
            "[UserFeedbackProcessor] execute started. action=%s flow=%s rewrite_scope=%s "
            "enable_local_source_trace=%s report_len=%s",
            action,
            flow_name,
            feedback.get("rewrite_scope", ""),
            enable_local_source_trace,
            len(report_content),
        )

        async def apply_trace_if_enabled(action_result: dict) -> dict:
            """按开关决定是否执行局部溯源。

            Args:
                action_result: 子处理器返回的改写结果。

            Returns:
                开关关闭时返回原始 action_result；开启时返回局部溯源增强后的结果。
            """
            if not enable_local_source_trace:
                logger.info(
                    "[UserFeedbackProcessor] local source trace skipped. action=%s flow=%s reason=disabled",
                    action,
                    flow_name,
                )
                return action_result
            logger.info(
                "[UserFeedbackProcessor] local source trace stage started. action=%s flow=%s",
                action,
                flow_name,
            )
            traced_result = await apply_local_source_trace_to_action_result(
                feedback=feedback,
                action_result=action_result,
                final_result=final_result,
                llm_model_name=self.llm_model_name,
                language=language,
            )
            logger.info(
                "[UserFeedbackProcessor] local source trace stage completed. action=%s flow=%s "
                "citation_updated=%s warning_present=%s",
                action,
                flow_name,
                "citation_messages" in traced_result,
                bool(traced_result.get("warning_info")),
            )
            return traced_result

        if action == "sync":
            synced_report = feedback["selected_text"]
            logger.info(
                "[UserFeedbackProcessor] sync flow completed. original_len=%s synced_len=%s",
                len(report_content),
                len(synced_report),
            )
            return {
                "sync_only": True,
                "new_report": synced_report,
                "original_text": report_content,
                "original_start_offset": 0,
                "original_end_offset": len(report_content),
                "rewritten_text": synced_report,
                "rewritten_start_offset": 0,
                "rewritten_end_offset": len(synced_report),
            }

        if action in SYNONYM_REWRITE_ACTIONS:
            action_result = await self._synonym_rewriter.synonym_rewrite(
                feedback=feedback,
                report_content=report_content,
                language=language,
            )
            return await apply_trace_if_enabled(action_result)

        if action == "supplementary_search":
            action_result = await self._supplementary_searcher.supplementary_search(
                feedback=feedback,
                final_result=final_result,
                language=language,
            )
            return await apply_trace_if_enabled(action_result)

        if action == "new_task":
            action_result = await self._new_task_processor.run_new_task(
                feedback=feedback,
                final_result=final_result,
                language=language,
            )
            return await apply_trace_if_enabled(action_result)

        if action == "truth_verification":
            return await self._truth_verifier.truth_verification(
                feedback=feedback,
                final_result=final_result,
                current_report=current_report,
                language=language,
            )

        # 后续根据不同的action，调用不同的处理逻辑

        raise CustomValueException(
            StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
            StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action),
        )
