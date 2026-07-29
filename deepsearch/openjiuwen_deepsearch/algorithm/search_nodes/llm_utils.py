# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.llm_utils import (
    ainvoke_llm_with_stats,
    format_llm_log_correlation_suffix,
    llm_error_chain_blob,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


@dataclass
class RunLLMConfig:

    config: dict
    system_prompt: str | None = None
    prompt_template_file: str | None = None
    context_vars: dict | None = None
    need_stream_out: bool = False
    agent_name: str | None = None
    tools: List[Dict[str, Any]] | None = None


async def run_llm(
    params: RunLLMConfig,
) -> Tuple[Union[str, Dict[str, Any]], Optional[str], int, int]:
    if params.prompt_template_file:
        messages = apply_system_prompt(
            prompt_template_file=params.prompt_template_file,
            context_vars=params.context_vars or {},
        )
    else:
        if not params.system_prompt:
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e="Either system_prompt or prompt_template_file must be provided"
                ),
            )
        messages = [
            {
                "role": "system",
                "content": params.system_prompt,
            }
        ]
        if params.context_vars and "messages" in params.context_vars:
            messages.extend(params.context_vars["messages"])
    (
        result,
        reasoning,
        total_input_tokens,
        total_output_tokens,
    ) = await _run_llm_via_ainvoke(
        messages=messages,
        config=params.config,
        need_stream_out=params.need_stream_out,
        agent_name=params.agent_name,
        extra_body=params.config.get("extra_body") or {},
        tools=params.tools,
    )

    return result, reasoning, total_input_tokens, total_output_tokens


_CONTEXT_LIMIT_PHRASES = (
    "context_length_exceeded",
    "context length",
    "context window",
    "maximum context",
    "maximum context length",
    "reduce the length of",
    "too many tokens",
    "input is too long",
    "input tokens exceed",
    "exceed the configured limit",
    "requested about",
    "text input",
    "compress your prompt",
    "context-compression",
    "token limit",
    "prompt is too long",
    "message is too long",
    "input too long",
)


def _is_context_limit_error(msg: str, chain_root: BaseException | None = None) -> bool:
    """Match provider text in the wrapped message and in ``__cause__``/``__context__`` chain.

    OpenJiuwen replaces many failures with ``status code is 400``; the real reason often
    lives on chained ``APIStatusError`` / ``HTTPError`` / response bodies.
    """
    lower = (msg or "").lower()
    if any(phrase in lower for phrase in _CONTEXT_LIMIT_PHRASES):
        return True
    if chain_root is not None:
        chain_lower = llm_error_chain_blob(chain_root).lower()
        return any(phrase in chain_lower for phrase in _CONTEXT_LIMIT_PHRASES)
    return False


async def _run_llm_via_ainvoke(
    *,
    messages: List[Dict],
    config: dict,
    need_stream_out: bool = False,
    agent_name: str | None = None,
    extra_body: dict | None = None,
    tools: List[Dict[str, Any]] | None = None,
) -> Tuple[Union[str, Dict[str, Any]], Optional[str], int, int]:
    max_tries = config.get("max_tries", 4)
    base_sleep_time = 1

    model_name = config.get("model_name")
    logger.info(f"max_tries: {max_tries}, base_sleep_time: {base_sleep_time}, model_name: {model_name}")

    if not model_name:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                e="model_name must be provided in config when llm is None"
            ),
        )
    try:
        ctx = llm_context.get()
        if ctx:
            llm = ctx.get(model_name)
        if llm is None:
            raise CustomValueException(
                StatusCode.LLM_INSTANCE_NONE_ERROR.code,
                (
                    StatusCode.LLM_INSTANCE_NONE_ERROR.errmsg.format(name=model_name)
                    if "{name}" in StatusCode.LLM_INSTANCE_NONE_ERROR.errmsg
                    else f"LLM with model_name '{model_name}' not found in llm_context"
                ),
            )
    except LookupError as e:
        raise CustomValueException(
            StatusCode.LLM_INSTANCE_NONE_ERROR.code,
            "llm_context not set. Please ensure LLM is registered before calling run_llm.",
        ) from e

    total_input_tokens = 0
    total_output_tokens = 0

    for attempt in range(max_tries):
        try:
            response = await ainvoke_llm_with_stats(
                llm=llm,
                messages=messages,
                llm_type="basic",
                agent_name=agent_name,
                tools=tools,
                need_stream_out=need_stream_out,
            )

            if isinstance(response, dict):
                resp_content = response.get("content") or ""
                usage = response.get("usage_metadata") or {}
                reason_content = response.get("reason_content") or response.get("reasoning_content") or ""
                resp_tool_calls = response.get("tool_calls") or []
            else:
                resp_content = getattr(response, "content", "") or ""
                um = getattr(response, "usage_metadata", None)
                usage = (um.model_dump() if hasattr(um, "model_dump") else um) if um else {}
                reason_content = (
                    getattr(response, "reason_content", None) or getattr(response, "reasoning_content", None) or ""
                )
                resp_tool_calls = getattr(response, "tool_calls", None) or []

            total_input_tokens = usage.get("input_tokens", 0) if usage else 0
            total_output_tokens = usage.get("output_tokens", 0) if usage else 0

            content = resp_content
            reasoning = None

            if config.get("append_think_tags_to_messages", False) and reason_content:
                reasoning = reason_content.strip()
                content = "<think>\n" + reasoning + "\n</think>" + (content or "")

            if resp_tool_calls:
                return (
                    {"content": content.strip() if content else "", "tool_calls": resp_tool_calls},
                    reasoning,
                    total_input_tokens,
                    total_output_tokens,
                )

            if content and content.strip():
                return (
                    content.strip(),
                    reasoning,
                    total_input_tokens,
                    total_output_tokens,
                )

            logger.warning(
                "Warning: Attempt %s received an empty response.%s",
                attempt + 1,
                format_llm_log_correlation_suffix(),
            )
        except CustomValueException as e:
            if _is_context_limit_error(e.message or str(e), getattr(e, "__cause__", None)):
                raise
            logger.warning(
                "Attempt %s failed (API error) agent=%s: %s%s",
                attempt + 1,
                agent_name or "-",
                "*" if LogManager.is_sensitive() else (e.message or str(e)),
                format_llm_log_correlation_suffix(),
            )
        except Exception as e:
            logger.warning(
                "Attempt %s failed agent=%s: %s%s",
                attempt + 1,
                agent_name or "-",
                "*" if LogManager.is_sensitive() else e,
                format_llm_log_correlation_suffix(),
                exc_info=not LogManager.is_sensitive(),
            )

        if attempt < max_tries - 1:
            sleep_time = min(base_sleep_time * (2**attempt), 30)
            await asyncio.sleep(sleep_time)
        else:
            logger.error(
                "Error: All retry attempts have been exhausted.%s",
                format_llm_log_correlation_suffix(),
            )

    raise CustomValueException(
        StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.code,
        StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.errmsg,
    )
