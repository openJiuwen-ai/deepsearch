# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import SYNONYM_REWRITE_ACTIONS
from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import strip_markup_in_range
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

ACTION_TO_PROMPT = {
    "expand": "synonym_rewrite_expand",
    "polish": "synonym_rewrite_polish",
    "shorten": "synonym_rewrite_shorten",
}


def get_llm_instance(llm_model_name: str):
    """从当前上下文中获取指定名称的 LLM 实例。"""
    all_llms = llm_context.get()
    return all_llms.get(llm_model_name)


class SynonymRewriter:
    """执行报告级别的同义改写，仅返回新的正文片段。"""

    def __init__(self, llm_model_name: str):
        self.llm_model_name = llm_model_name

    @staticmethod
    def get_prompt_name(action: str) -> str:
        """将前端动作名映射为对应的提示模板名称。"""
        if action not in ACTION_TO_PROMPT:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.errmsg.format(action=action),
            )
        return ACTION_TO_PROMPT[action]

    async def synonym_rewrite(
        self,
        feedback: dict,
        report_content: str,
        language: str,
    ) -> dict:
        """执行报告级别的同义改写。

        Args:
            feedback: 用户反馈信息。
            report_content: 当前报告正文。
            language: 当前报告语言。

        Returns:
            dict: 仅包含正文改写结果与替换区间信息。
        """
        action = feedback["action"]
        start_offset = feedback["start_offset"]
        end_offset = feedback["end_offset"]
        user_instruction = feedback.get("user_instruction", "")

        stripped_text, _, _ = strip_markup_in_range(report_content, start_offset, end_offset)

        removed_markup_len = len(report_content) - len(stripped_text)
        stripped_end = end_offset - removed_markup_len
        original_text_clean = stripped_text[start_offset:stripped_end]

        rewritten_text = await self._generate_synonym_rewrite_text(
            action=action,
            original_text=original_text_clean,
            language=language,
            user_instruction=user_instruction,
        )

        new_report = stripped_text[:start_offset] + rewritten_text + stripped_text[stripped_end:]
        rewritten_end_offset = start_offset + len(rewritten_text)

        return dict(
            new_report=new_report,
            original_text=feedback["selected_text"],
            original_start_offset=start_offset,
            original_end_offset=end_offset,
            original_text_clean=original_text_clean,
            rewritten_text=rewritten_text,
            rewritten_start_offset=start_offset,
            rewritten_end_offset=rewritten_end_offset,
        )

    async def _generate_synonym_rewrite_text(
        self,
        action: str,
        original_text: str,
        language: str,
        user_instruction: str = "",
    ) -> str:
        """调用 LLM 执行一次改写。

        入参 `original_text` 应为已经剥离引用标记的纯文本，
        这样可以避免模型改写 `[[n]](url)` 之类的结构化内容。
        """
        try:
            prompt_name = self.get_prompt_name(action)
            context_vars = {
                "original_text": original_text,
                "language": language,
                "user_instruction": user_instruction,
            }
            messages = apply_system_prompt(prompt_name, context_vars)

            llm = get_llm_instance(self.llm_model_name)

            response = await ainvoke_llm_with_stats(
                llm=llm,
                messages=messages,
                agent_name=AgentLlmName.USER_FEEDBACK_PROCESSOR_SYNONYM_REWRITER.value,
            )
        except CustomException:
            raise
        except Exception as error:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=str(error)),
            ) from error
        rewritten_text = response.get("content", "") if isinstance(response, dict) else str(response)

        logger.info(f"[SynonymRewriter] action={action}, original_len={len(original_text)}, "
                    f"rewritten_len={len(rewritten_text)}")
        if not LogManager.is_sensitive():
            logger.info(f"[SynonymRewriter] action={action}, original_text={original_text}, "
                        f"rewritten_text={rewritten_text}")
        return rewritten_text
