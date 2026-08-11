# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    llm_context,
    model_context,
    session_context,
)


def resolve_session_collector():
    """从上下文变量中获取当前会话对象。"""
    try:
        return session_context.get()
    except LookupError:
        return None


def resolve_model_context_collector():
    """从上下文变量中获取当前模型上下文对象。"""
    try:
        return model_context.get()
    except LookupError:
        return None


def normalize_user_feedback_agent_name(agent_name_or_suffix: str) -> str:
    """生成用户反馈处理链路的完整 LLM 调用点名称。

    Args:
        agent_name_or_suffix: 完整的 agent_name，或省略 ``user_feedback_processor_`` 前缀的后缀。

    Returns:
        完整且可被 ``AgentLlmName`` 校验的用户反馈处理器 agent_name。
    """
    user_feedback_prefix = f"{NodeId.USER_FEEDBACK_PROCESSOR.value}_"
    if agent_name_or_suffix == NodeId.USER_FEEDBACK_PROCESSOR.value:
        return agent_name_or_suffix
    if agent_name_or_suffix.startswith(user_feedback_prefix):
        return agent_name_or_suffix
    return f"{user_feedback_prefix}{agent_name_or_suffix}"


async def invoke_user_feedback_prompt(
    llm_model_name: str,
    prompt_name: str,
    context_vars: dict[str, Any],
    agent_name_or_suffix: str,
) -> str:
    """应用系统模板并调用用户反馈处理链路中的 LLM。

    Args:
        llm_model_name: 从上下文中获取 LLM 实例时使用的模型名称。
        prompt_name: prompt 模板名称。
        context_vars: prompt 模板变量。
        agent_name_or_suffix: 完整 agent_name 或用户反馈处理器下的调用点后缀。

    Returns:
        LLM 返回的文本内容。
    """
    messages = apply_system_prompt(prompt_name, context_vars)
    llm = llm_context.get().get(llm_model_name)
    response = await ainvoke_llm_with_stats(
        llm=llm,
        messages=messages,
        agent_name=normalize_user_feedback_agent_name(agent_name_or_suffix),
    )
    return response.get("content", "") if isinstance(response, dict) else str(response)


class UserFeedbackPromptInvoker:
    """为用户反馈处理器提供统一的 prompt 调用入口。"""

    llm_model_name: str

    async def _invoke_prompt(self, prompt_name: str, context_vars: dict[str, Any], agent_name_or_suffix: str) -> str:
        """应用系统模板并调用 LLM。

        Args:
            prompt_name: prompt 模板名称。
            context_vars: prompt 模板变量。
            agent_name_or_suffix: 完整 agent_name 或用户反馈处理器下的调用点后缀。

        Returns:
            LLM 返回的文本内容。
        """
        return await invoke_user_feedback_prompt(
            llm_model_name=self.llm_model_name,
            prompt_name=prompt_name,
            context_vars=context_vars,
            agent_name_or_suffix=agent_name_or_suffix,
        )
