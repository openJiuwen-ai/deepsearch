# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from typing import Sequence, Any

import json_repair
from openjiuwen.core.foundation.llm.schema.message import UserMessage, SystemMessage, AssistantMessage, ToolMessage
from pydantic import BaseModel

from openjiuwen_deepsearch.common.common_constants import MAX_LLM_RESP_LENGTH
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Message
from openjiuwen_deepsearch.utils.common_utils.stream_utils import get_current_time, MessageType, StreamEvent
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context, cancel_context
from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.log_utils.log_metrics import metrics_logger, TIME_LOGGER_TAG

logger = logging.getLogger(__name__)


def _raise_if_cancelled():
    """
    检查 cancel_context 中的取消事件，如果已设置则抛出 CancelledError。
    
    此函数在 LLM 调用的关键路径（llm_astream / ainvoke_llm_with_stats）中被调用，
    用于及时响应外部取消请求，中断正在进行的 LLM 流式/非流式调用。
    """
    cancel_event = cancel_context.get()
    if cancel_event and cancel_event.is_set():
        logger.info("LLM call cancelled via cancel_event")
        raise asyncio.CancelledError("cancelled")


def messages_to_json(messages: Sequence[Any] | Message) -> str:
    """Dump message to json string."""
    result = []
    if messages is None:
        return ""

    if isinstance(messages, Message):
        result = messages.model_dump()
    else:
        for msg in messages:
            if isinstance(msg, dict):
                result.append(msg)
            elif isinstance(msg, Message):
                result.append(msg.model_dump())
            else:
                result.append(str(msg))
                if not LogManager.is_sensitive():
                    logger.error(f"error message type: {msg}")
                else:
                    logger.error(f"error message type.")

    return json.dumps(result, ensure_ascii=False, indent=4)


def normalize_json_output(input_data: str) -> str:
    """
    规范化 JSON 输出

    Args:
        input_data: 可能包含 JSON 的字符串内容

    Returns:
        str: 规范化的 JSON 字符串，如果不是 JSON, 则为原始内容
    """
    processed = input_data.strip()
    json_signals = ('{', '[', '```json', '```ts')

    if not any(indicator in processed for indicator in json_signals[:2]) and not any(
            marker in processed for marker in json_signals[2:]):
        return processed

    # 处理代码块标记
    code_blocks = {
        'prefixes': ('```json', '```ts'),
        'suffix': '```'
    }
    for prefix in code_blocks['prefixes']:
        if processed.startswith(prefix):
            processed = processed[len(prefix):].lstrip('\n')

    if processed.endswith(code_blocks['suffix']):
        processed = processed[:-len(code_blocks['suffix'])].rstrip('\n')

    # 尝试进行JSON修复和序列化
    try:
        reconstructed = json_repair.loads(processed)
        return json.dumps(reconstructed, ensure_ascii=False)
    except Exception as error:
        if not LogManager.is_sensitive():
            logger.error(f"JSON normalization error: {error}")
        else:
            logger.error(f"JSON normalization error.")
        return input_data.strip()


def _extract_json(text: str) -> str:
    # 去除 ```json 或 ``` 包裹
    return re.sub(r"^```(?:json)?\n|\n```$", "", text.strip())


async def llm_astream(llm, messages, model_name, agent_name, tools=None, need_stream_out=False,
                      stream_meta: dict | None = None):
    """
        description: llm async astream

        Args:
                llm: llm instance
                messages: llm inputs
                model_name: llm model name
                agent_name: agent name
                tools: tools to bind for llm
                need_stream_out: need write llm output stream out

        Returns:
                response
    """
    _raise_if_cancelled()
    full_chunk = None
    can_write_stream = True
    session = None
    try:
        session = session_context.get()
        if session is None:
            can_write_stream = False
            logger.debug(f"session_context not set, can not write to stream")
    except LookupError:
        can_write_stream = False
        logger.debug(f"session_context not set, can not write to stream")

    def _make_payload(message_id: str, event: str, content: str = "") -> dict:
        payload = {
            "message_id": message_id,
            "agent": agent_name,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": event,
            "created_time": get_current_time()
        }
        if stream_meta:
            payload.update(dict(stream_meta))
        return payload

    stream_id = None
    if can_write_stream and need_stream_out:
        stream_id = str(uuid.uuid4())
        await session.write_custom_stream(_make_payload(stream_id, StreamEvent.START.value, ""))

    try:
        async for chunk in llm.stream(messages=messages, model=model_name, tools=tools):
            _raise_if_cancelled()
            if full_chunk is None:
                full_chunk = chunk
            else:
                full_chunk += chunk
                if len(full_chunk.content) >= MAX_LLM_RESP_LENGTH:
                    logger.warning(
                        f"[llm_astream] llm response is too long, truncate to {MAX_LLM_RESP_LENGTH} characters")
                    full_chunk.content = full_chunk.content[:MAX_LLM_RESP_LENGTH]
                    break
            if can_write_stream and need_stream_out:
                await session.write_custom_stream(_make_payload(stream_id, StreamEvent.MESSAGE.value, chunk.content))
    except Exception as e:
        if can_write_stream and need_stream_out:
            await session.write_custom_stream(_make_payload(stream_id, StreamEvent.DONE.value, ""))
        raise e

    if can_write_stream and need_stream_out:
        await session.write_custom_stream(_make_payload(stream_id, StreamEvent.DONE.value, ""))

    if full_chunk is None:
        logger.error(f"[llm_astream] llm response is None")
        raise CustomValueException(
            error_code=StatusCode.LLM_RESPONSE_NONE.code,
            message=StatusCode.LLM_RESPONSE_NONE.errmsg)
    return full_chunk


async def ainvoke_llm_with_stats(llm, messages, llm_type: str = "basic", agent_name="AI", schema=None, tools=None,
                                 need_stream_out=False, stream_meta: dict | None = None):
    """
    description: llm async invoke tool

    Args:
            llm: llm instance
            messages: llm inputs
            llm_type: llm type, default "basic"
            schema: construct output class
            tools: tools to bind for llm
            need_stream_out: need write llm output stream out

    Returns:
            dict response if schema is None, construct output if with schema
    """
    _raise_if_cancelled()
    if not llm:
        raise CustomValueException(
            error_code=StatusCode.LLM_INSTANCE_NONE_ERROR.code,
            message=StatusCode.LLM_INSTANCE_NONE_ERROR.errmsg)
    stats_info_llm = Config().service_config.stats_info_llm

    # get model_name
    if not llm_type.strip():
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code,
            message=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.errmsg.format(param="llm_type"))

    model_name = llm.get("model_name", "")
    if not model_name:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code,
            message=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.errmsg.format(param="model_name"))

    start = None
    if stats_info_llm:
        start = time.time()

    # 真正调用llm处
    messages = transfer_to_jiuwen_messages(messages)
    llm_model = llm.get("model", None)
    if llm_model is None:
        raise CustomValueException(
            error_code=StatusCode.LLM_INSTANCE_NONE_ERROR.code,
            message=StatusCode.LLM_INSTANCE_NONE_ERROR.errmsg)
    response = await llm_astream(llm=llm_model, messages=messages,
                                 model_name=model_name, agent_name=agent_name, tools=tools,
                                 need_stream_out=need_stream_out, stream_meta=stream_meta)

    if stats_info_llm:
        duration = time.time() - start

        # get usage token usage info
        usage = (
            response.usage_metadata
            if isinstance(response.usage_metadata, dict)
            else response.usage_metadata.model_dump()
            if isinstance(response.usage_metadata, BaseModel)
            else {}
        )
        llm_stat = {
            "method_name": agent_name,
            "duration": duration,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_latency", 0)
        }
        metrics_logger.info(
            f"{TIME_LOGGER_TAG} thread_id: {session_id_ctx.get()} ------ [LLM CALL STATISTICS]: {llm_stat}"
        )

    response.content = _extract_json(response.content)
    if schema is not None:
        response = schema.model_validate_json(response.content)
        return response
    return _unify_responnse(response)


def _unify_responnse(response):
    temp_response = response.model_dump()
    new_response = copy.deepcopy(temp_response)
    if temp_response.get("tool_calls"):
        tool_calls = temp_response.get("tool_calls")
        for idx, tool_call in enumerate(tool_calls):
            func = tool_call.get("function")
            if not tool_call.get("args") and func and func.get("arguments"):
                arguments = normalize_json_output(func.get("arguments"))
                new_response.get("tool_calls")[idx]["args"] = json.loads(arguments)
            if func and func.get("name"):
                new_response.get("tool_calls")[idx]["name"] = func.get("name")
            if tool_call.get("type"):
                new_response.get("tool_calls")[idx]["type"] = "tool_call"
            new_response.get("tool_calls")[idx].pop("index", None)
    return new_response


def transfer_to_jiuwen_messages(origin_messages: list):
    """转换消息类型"""
    output_messages = []
    for message in origin_messages:
        if isinstance(message, dict):
            role = message.get("role", "")
            content = message.get("content", "")
            name = message.get("name", "")
            if role == "system":
                output_messages.append(SystemMessage(content=content, name=name))
            elif role == "user":
                output_messages.append(UserMessage(content=content, name=name))
            elif role == "assistant":
                output_messages.append(
                    AssistantMessage(
                        content=content,
                        name=name,
                        tool_calls=message.get("tool_calls", []),
                        usage_metadata=message.get("usage_metadata", None),
                        reasoning_content=message.get("reason_content", "")
                    )
                )
            elif role == "tool":
                output_messages.append(
                    ToolMessage(content=content, name=name,
                                tool_call_id=message.get("tool_call_id", "") or f"call_{str(uuid.uuid4().hex[:22])}")
                )
            else:
                logger.error(f"role:{role} not support")
        elif isinstance(message, BaseModel):
            output_messages.append(message)
        else:
            logger.error(f"message type:{type(message)} not support")

    return output_messages


def record_llm_retry_log(current_try=0, max_retries=3, section_idx=None,
                         step_title=None, operation=None, error=None, extra_info=None):
    """Record the retry log of LLM."""
    if LogManager.is_sensitive():
        if current_try < max_retries:
            msg = (f"section_idx: {section_idx} | "
                   f"Error when {operation} | "
                   f"retry , number of retries: {current_try} / {max_retries}")
            logger.warning(f"{msg}")
        else:
            msg = (f"section_idx: {section_idx} | "
                   f"Error when {operation} | "
                   f"Failed to {operation}, the max retries have been reached, max retry : {max_retries}")
            logger.error(f"{msg}")
    else:
        error_detail = f"{error}" if error else ""

        if current_try < max_retries:
            msg = (f"section_idx: {section_idx} | step title: {step_title} | "
                   f"Error when {operation}: {error_detail} | "
                   f"Extra Info: {extra_info} | "
                   f"retry , number of retries: {current_try} / {max_retries}")
            logger.warning(msg, exc_info=error is not None)
        else:
            msg = (f"section_idx: {section_idx} | step title: {step_title} | "
                   f"Error when {operation}: {error_detail} | "
                   f"Extra Info: {extra_info} | "
                   f"Failed to {operation}, the max retries have been reached, max retry : {max_retries}")
            logger.error(msg, exc_info=error is not None)
