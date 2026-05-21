# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import copy
import inspect
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Sequence, Any

import json_repair
from openjiuwen.core.foundation.llm.schema.message import (
    UserMessage,
    SystemMessage,
    AssistantMessage,
    ToolMessage,
    UsageMetadata,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from pydantic import BaseModel

from openjiuwen_deepsearch.common.common_constants import MAX_LLM_RESP_LENGTH
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Message
from openjiuwen_deepsearch.utils.common_utils.stream_utils import get_current_time, MessageType, StreamEvent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context, cancel_context
from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.log_utils.log_metrics import metrics_logger, TIME_LOGGER_TAG

logger = logging.getLogger(__name__)
DEFAULT_AGENT_NAME = "AI"
_ALLOWED_AGENT_NAMES = frozenset({DEFAULT_AGENT_NAME, *(item.value for item in AgentLlmName)})


def format_llm_log_correlation_suffix() -> str:
    """Suffix for LLM logs: per-run directory basename (e.g. result_<conversation_id>) and absolute path."""
    try:
        rt = session_context.get()
    except LookupError:
        return ""
    if rt is None:
        return ""
    ld = rt.get_global_state("log_dir")
    if not ld:
        cfg = rt.get_global_state("config") or {}
        if isinstance(cfg, dict):
            ld = cfg.get("log_dir") or ""
    if not ld:
        return ""
    if LogManager.is_sensitive():
        return " result_run=***"
    base = os.path.basename(os.path.normpath(str(ld)))
    return f" result_run={base} log_dir={os.path.abspath(str(ld))}"


_DEEPSEARCH_NODE_IDS = frozenset({
    NodeId.INITIAL_STATE.value,
    NodeId.FIND_ACTION_SPACE.value,
    NodeId.RUN_ACTION.value,
    NodeId.VALIDATE_NEW_STATE.value,
    NodeId.TOOL.value,
    NodeId.SIMPLE_REACT_SEARCH.value,
})


_WORKFLOW_LLM_USAGE: dict[str, dict[str, Any]] = {}
_USAGE_ONLY_PARSER_PATCHES: dict[int, dict[str, Any]] = {}
_AGENT_LLM_TIMEOUT_MAX_SECONDS = 3600


def _clamp_agent_llm_timeout(timeout: Any) -> int:
    """将调用方提供的 agent LLM timeout 限制在服务端策略上限内。
    Args:
        timeout: 原始超时值。
    Returns:
        int: 裁剪后的超时值。
    """
    try:
        parsed_timeout = int(timeout)
    except (TypeError, ValueError):
        # 非法值转为 0，表示关闭业务层超时，回退到底层控制
        return 0
    # 负数转为 0，保留关闭语义
    if parsed_timeout < 0:
        return 0

    # 裁剪上限，防止极大值绕过底层超时控制
    return min(parsed_timeout, _AGENT_LLM_TIMEOUT_MAX_SECONDS)


def normalize_agent_llm_timeouts(value: Any) -> dict[str, int]:
    """规范化按 agent 配置的 LLM 总超时字典。

    缺少 ``default`` 时仍保持未启用语义；一旦启用：
    - 0 值表示关闭业务层墙钟超时，回退到底层 SDK 的 ServiceConfig.llm_timeout 控制
    - 极大值会被裁剪到上限，防止绕过底层超时控制

    Args:
        value: 原始超时配置。

    Returns:
        dict[str, int]: 规范化后的超时配置；缺少 ``default`` 时返回空字典表示未启用。
    """
    if not isinstance(value, dict) or not value or "default" not in value:
        return {}
    normalized_value: dict[str, int] = {}
    for agent_key, timeout in value.items():
        normalized_value[agent_key] = _clamp_agent_llm_timeout(timeout)
    return normalized_value


@dataclass(frozen=True)
class ResolvedAgentTimeout:
    """描述一次 agent LLM 总超时的解析结果。

    Attributes:
        timeout: 最终命中的超时时间，单位秒。
        matched_by: 命中来源，取值为 ``agent_name``、``node_key`` 或 ``default``。
        matched_key: 实际命中的配置 key。
        resolved_node_key: 从 ``agent_name`` 解析出的节点级 key；无法解析时为 ``None``。
    """

    timeout: int
    matched_by: str
    matched_key: str
    resolved_node_key: str | None


@dataclass(frozen=True)
class _ConsumeLlmStreamRequest:
    """封装流消费阶段所需的上下文参数。

    Attributes:
        llm: LLM 对象。
        stream_kwargs: 传给 ``llm.stream`` 的参数。
        can_write_stream: 是否允许写自定义流。
        need_stream_out: 是否需要输出流式消息。
        session: 当前 session。
        stream_id: 当前流 ID。
        stream_meta: 附加流元数据。
        agent_name: 当前 agent 名称。
    """

    llm: Any
    stream_kwargs: dict[str, Any]
    can_write_stream: bool
    need_stream_out: bool
    session: Any
    stream_id: str | None
    stream_meta: dict[str, Any] | None
    agent_name: str


def _normalize_agent_name(agent_name: Any) -> str:
    """标准化 agent_name 字段。

    Args:
        agent_name (Any): 原始 agent_name 值。

    Returns:
        str: 标准化后的 agent_name；为空时返回 "unknown"。
    """
    if not isinstance(agent_name, str):
        return "unknown"
    normalized_name = agent_name.strip()
    return normalized_name if normalized_name else "unknown"


def _validate_invoke_agent_name(agent_name: Any) -> str:
    """校验并规范化 LLM 调用入口的 agent_name。

    Args:
        agent_name: 调用方传入的原始 agent_name。

    Returns:
        规范化后的 agent_name；None 或空白字符串返回默认值。

    Raises:
        CustomValueException: agent_name 既不是空值，也不是默认值或 AgentLlmName 中定义的值时抛出。
    """
    if agent_name is None:
        return DEFAULT_AGENT_NAME
    if not isinstance(agent_name, str):
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code,
            message=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.errmsg.format(param="agent_name"),
        )

    normalized_name = agent_name.strip()
    if not normalized_name:
        return DEFAULT_AGENT_NAME
    if normalized_name in _ALLOWED_AGENT_NAMES:
        return normalized_name
    raise CustomValueException(
        error_code=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code,
        message=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.errmsg.format(param="agent_name"),
    )


def _resolve_node_agent_key(agent_name: str) -> str | None:
    """按最长前缀从 agent_name 中解析节点级 key。

    Args:
        agent_name: 原始 agent_name。

    Returns:
        str | None: 解析得到的 ``NodeId.value``；无法匹配时返回 ``None``。
    """
    normalized_agent_name = _normalize_agent_name(agent_name)
    for node_key in sorted((item.value for item in NodeId), key=len, reverse=True):
        if normalized_agent_name.startswith(node_key):
            return node_key
    return None


def _resolve_agent_llm_timeout(agent_name: str, session: Any = None) -> ResolvedAgentTimeout | None:
    """解析当前 agent 应使用的 wall-clock timeout。

    Args:
        agent_name: 原始 agent_name。
        session: 当前 session 对象。

    Returns:
        ResolvedAgentTimeout | None: 命中的超时解析结果；未配置时返回 ``None``。
    """
    if session is None:
        return None
    try:
        timeout_config = session.get_global_state("config.agent_llm_timeouts")
    except Exception:
        return None
    timeout_config = normalize_agent_llm_timeouts(timeout_config)
    if not timeout_config:
        return None

    normalized_agent_name = _normalize_agent_name(agent_name)
    if normalized_agent_name in timeout_config:
        return ResolvedAgentTimeout(
            timeout=timeout_config[normalized_agent_name],
            matched_by="agent_name",
            matched_key=normalized_agent_name,
            resolved_node_key=_resolve_node_agent_key(normalized_agent_name),
        )

    resolved_node_key = _resolve_node_agent_key(normalized_agent_name)
    if resolved_node_key and resolved_node_key in timeout_config:
        return ResolvedAgentTimeout(
            timeout=timeout_config[resolved_node_key],
            matched_by="node_key",
            matched_key=resolved_node_key,
            resolved_node_key=resolved_node_key,
        )

    if "default" in timeout_config:
        return ResolvedAgentTimeout(
            timeout=timeout_config["default"],
            matched_by="default",
            matched_key="default",
            resolved_node_key=resolved_node_key,
        )
    return None


def _build_empty_agent_name_usage(agent_name: str) -> dict[str, Any]:
    """构造单个 agent_name 的空 token 统计结构。

    Args:
        agent_name (str): 调用方标识。

    Returns:
        dict[str, Any]: 单 agent 的空统计结构。
    """
    return {
        "agent_name": _normalize_agent_name(agent_name),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,
    }


def _merge_agent_name_usage_list(agent_usage_list: Any) -> list[dict[str, Any]]:
    """合并并规范化 agent_name 级 token 统计列表。

    Args:
        agent_usage_list (Any): 待处理的 agent 统计列表。

    Returns:
        list[dict[str, Any]]: 去重并聚合后的统计列表。
    """
    if not isinstance(agent_usage_list, list):
        return []
    merged_usage: dict[str, dict[str, Any]] = {}
    for usage_item in agent_usage_list:
        if not isinstance(usage_item, dict):
            continue
        normalized_name = _normalize_agent_name(usage_item.get("agent_name"))
        current_usage = merged_usage.setdefault(normalized_name, _build_empty_agent_name_usage(normalized_name))
        current_usage["input_tokens"] += _to_non_negative_int(usage_item.get("input_tokens", 0))
        current_usage["output_tokens"] += _to_non_negative_int(usage_item.get("output_tokens", 0))
        current_usage["total_tokens"] += _to_non_negative_int(usage_item.get("total_tokens", 0))
        current_usage["llm_call_count"] += _to_non_negative_int(usage_item.get("llm_call_count", 0))
    return list(merged_usage.values())


def _build_empty_workflow_llm_usage() -> dict[str, Any]:
    """构造空的 workflow 级 token 统计结构。

    Returns:
        dict[str, Any]: 空统计结构，包含总量和 agent_name 统计字段。
    """
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,
        "agent_name_token_usage": [],
    }


def normalize_workflow_llm_usage(usage: Any) -> dict[str, Any]:
    """标准化 workflow 级 token 统计结构。

    Args:
        usage (Any): 待标准化的数据，可为 dict 或任意类型。

    Returns:
        dict[str, Any]: 规范后的统计结构，包含总量统计和 agent_name 维度统计。
    """
    if not isinstance(usage, dict):
        return _build_empty_workflow_llm_usage()
    return {
        "input_tokens": _to_non_negative_int(usage.get("input_tokens", 0)),
        "output_tokens": _to_non_negative_int(usage.get("output_tokens", 0)),
        "total_tokens": _to_non_negative_int(usage.get("total_tokens", 0)),
        "llm_call_count": _to_non_negative_int(usage.get("llm_call_count", 0)),
        "agent_name_token_usage": _merge_agent_name_usage_list(usage.get("agent_name_token_usage", [])),
    }


def is_workflow_llm_usage_empty(usage: dict[str, Any]) -> bool:
    """判断 workflow 级 token 统计是否为空。

    Args:
        usage (dict[str, Any]): token 统计结构。

    Returns:
        bool: 全字段为 0 返回 True。
    """
    normalized_usage = normalize_workflow_llm_usage(usage)
    return (
        normalized_usage["input_tokens"] == 0
        and normalized_usage["output_tokens"] == 0
        and normalized_usage["total_tokens"] == 0
        and normalized_usage["llm_call_count"] == 0
        and len(normalized_usage["agent_name_token_usage"]) == 0
    )


def _ensure_workflow_llm_usage_initialized(session_id: str, session: Any = None) -> None:
    """确保指定 workflow 的本地累计状态已初始化。

    该方法用于跨进程恢复场景：当本地内存没有 session_id 对应累计时，尝试从
    session 全局状态中的 `search_context.final_result.workflow_llm_token_usage` 恢复。

    Args:
        session_id (str): workflow 对应会话 ID。
        session (Any): 当前会话对象，需支持 get_global_state 方法。
    """
    if not session_id or session_id == "-":
        return

    current_usage = _WORKFLOW_LLM_USAGE.get(session_id)
    if current_usage is not None and not is_workflow_llm_usage_empty(current_usage):
        return

    restored_usage = _build_empty_workflow_llm_usage()
    if session is not None:
        try:
            snapshot = session.get_global_state("search_context.final_result.workflow_llm_token_usage")
            restored_usage = normalize_workflow_llm_usage(snapshot)
        except Exception:
            # 兜底保护：恢复失败时不影响主流程，按空统计继续。
            restored_usage = _build_empty_workflow_llm_usage()

    _WORKFLOW_LLM_USAGE[session_id] = restored_usage


def get_effective_workflow_llm_usage(session_id: str, session: Any = None) -> dict[str, Any]:
    """获取当前 workflow 的有效 token 汇总快照。

    优先返回本地内存中的累计统计；当本地为空时，回退读取 session 中持久化的
    `search_context.final_result.workflow_llm_token_usage`，以覆盖 HITL 恢复等场景。

    Args:
        session_id (str): workflow 对应会话 ID。
        session (Any): 当前会话对象，需支持 get_global_state 方法；为空时仅返回本地累计。

    Returns:
        dict[str, Any]: 当前有效统计快照。
    """
    local_usage = normalize_workflow_llm_usage(get_workflow_llm_usage(session_id))
    if session is None or not is_workflow_llm_usage_empty(local_usage):
        return local_usage

    try:
        persisted_usage = normalize_workflow_llm_usage(
            session.get_global_state("search_context.final_result.workflow_llm_token_usage")
        )
    except Exception:
        persisted_usage = _build_empty_workflow_llm_usage()

    if not is_workflow_llm_usage_empty(persisted_usage):
        return persisted_usage
    return local_usage


def save_workflow_llm_usage_to_session(session: Any, session_id: str) -> dict[str, Any]:
    """将 workflow 级 token 累计落盘到 session 全局状态。

    Args:
        session (Any): 当前会话对象，需支持 update_global_state 方法。
        session_id (str): workflow 对应会话 ID。

    Returns:
        dict[str, Any]: 当前累计统计快照。
    """
    usage = get_effective_workflow_llm_usage(session_id=session_id, session=session)
    if session is None:
        return usage
    try:
        session.update_global_state({"search_context.final_result.workflow_llm_token_usage": usage})
    except Exception as e:
        # 持久化失败时仅降级，不影响主流程执行。
        logger.debug("Exception when updating session's global state: %s", e, exc_info=True)
    return usage


def add_workflow_llm_usage(
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    agent_name: str = "",
) -> None:
    """累加指定 workflow 的 LLM token 消耗。

    Args:
        session_id (str): workflow 对应会话 ID。
        input_tokens (int): 本次调用输入 token 数。
        output_tokens (int): 本次调用输出 token 数。
        total_tokens (int): 本次调用总 token 数。
        agent_name (str): 本次调用的 agent 名称。
    """
    if not session_id or session_id == "-":
        return

    usage = _WORKFLOW_LLM_USAGE.setdefault(session_id, _build_empty_workflow_llm_usage())
    usage["input_tokens"] += _to_non_negative_int(input_tokens)
    usage["output_tokens"] += _to_non_negative_int(output_tokens)
    usage["total_tokens"] += _to_non_negative_int(total_tokens)
    usage["llm_call_count"] += 1
    if agent_name:
        normalized_name = _normalize_agent_name(agent_name)
        agent_usage_list = usage.setdefault("agent_name_token_usage", [])
        if not isinstance(agent_usage_list, list):
            agent_usage_list = []
            usage["agent_name_token_usage"] = agent_usage_list
        target_usage = None
        for usage_item in agent_usage_list:
            if isinstance(usage_item, dict) and usage_item.get("agent_name") == normalized_name:
                target_usage = usage_item
                break
        if target_usage is None:
            target_usage = _build_empty_agent_name_usage(normalized_name)
            agent_usage_list.append(target_usage)
        target_usage["input_tokens"] += _to_non_negative_int(input_tokens)
        target_usage["output_tokens"] += _to_non_negative_int(output_tokens)
        target_usage["total_tokens"] += _to_non_negative_int(total_tokens)
        target_usage["llm_call_count"] += 1


def get_workflow_llm_usage(session_id: str) -> dict[str, Any]:
    """获取指定 workflow 的 LLM token 汇总信息。

    Args:
        session_id (str): workflow 对应会话 ID。

    Returns:
        dict[str, Any]: 汇总统计；若不存在返回全 0 结构。
    """
    if not session_id or session_id == "-":
        return _build_empty_workflow_llm_usage()
    usage = _WORKFLOW_LLM_USAGE.get(session_id)
    if usage is None:
        return _build_empty_workflow_llm_usage()
    return copy.deepcopy(usage)


def pop_workflow_llm_usage(session_id: str) -> dict[str, Any]:
    """弹出并返回指定 workflow 的 LLM token 汇总信息。

    Args:
        session_id (str): workflow 对应会话 ID。

    Returns:
        dict[str, Any]: 汇总统计；若不存在返回全 0 结构。
    """
    if not session_id or session_id == "-":
        return _build_empty_workflow_llm_usage()
    usage = _WORKFLOW_LLM_USAGE.pop(session_id, None)
    if usage is None:
        return _build_empty_workflow_llm_usage()
    return usage


def _to_non_negative_int(value: Any, default: int = 0) -> int:
    """将任意数值安全转换为非负整数。

    Args:
        value (Any): 待转换值，支持 int/float/str 等可转为数字的类型。
        default (int): 转换失败时的默认值。

    Returns:
        int: 非负整数；转换失败时返回 default。
    """
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _to_dict_safe(value: Any) -> dict[str, Any]:
    """将对象安全转换为字典。

    Args:
        value (Any): 任意对象，可能为 ``dict``、Pydantic 模型或普通对象。

    Returns:
        dict[str, Any]: 可用字典；转换失败时返回空字典。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            return {}
        return dumped if isinstance(dumped, dict) else {}
    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        return value_dict
    return {}


def _extract_usage_tokens(usage_payload: Any) -> tuple[int, int, int]:
    """从多种 usage 结构中提取输入、输出、总 token 数。

    Args:
        usage_payload (Any): usage 对象，可能为 dict、Pydantic 模型或 SDK 对象。

    Returns:
        tuple[int, int, int]: ``(input_tokens, output_tokens, total_tokens)``。
    """
    usage = _to_dict_safe(usage_payload)

    # 兼容部分供应商可能返回的嵌套 token_usage 结构。
    token_usage = usage.get("token_usage")
    if isinstance(token_usage, dict):
        merged_usage = dict(token_usage)
        merged_usage.update(usage)
        usage = merged_usage

    input_tokens = _to_non_negative_int(
        usage.get("input_tokens", usage.get("prompt_tokens", usage.get("prompt_token_count", 0)))
    )
    if input_tokens == 0:
        input_tokens = _to_non_negative_int(usage.get("prompt_tokens", usage.get("prompt_token_count", 0)))

    output_tokens = _to_non_negative_int(
        usage.get("output_tokens", usage.get("completion_tokens", usage.get("completion_token_count", 0)))
    )
    if output_tokens == 0:
        output_tokens = _to_non_negative_int(usage.get("completion_tokens", usage.get("completion_token_count", 0)))
    total_tokens = usage.get("total_tokens", usage.get("total_token_count"))
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    total_tokens = _to_non_negative_int(total_tokens, default=input_tokens + output_tokens)
    return input_tokens, output_tokens, total_tokens


def _is_llm_stats_enabled() -> bool:
    """判断当前调用是否开启 LLM 调用统计。

    优先级：
    1. 当前会话中的 `config.stats_info_llm`（按单次 workflow 生效）。
    2. 全局默认配置 `Config().agent_config.stats_info_llm`。

    Returns:
        bool: 是否开启 LLM 调用统计。
    """
    try:
        session = session_context.get()
        if session is not None:
            session_flag = session.get_global_state("config.stats_info_llm")
            if session_flag is not None:
                return bool(session_flag)
    except Exception as e:
        # 非 workflow 会话场景，走全局默认配置兜底。
        # 避免统计开关读取异常影响主流程。
        logger.debug("Exception when checking whether llm stats is enabled: %s", e, exc_info=True)

    return bool(Config().agent_config.stats_info_llm)


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


def _single_provider_error_detail(
    exc: BaseException, *, max_response_text: int = 16_000
) -> dict[str, Any]:
    """Same fields the OpenAI Python SDK exposes on API errors (cf. openai.APIStatusError).

    Mirrors ``test.test._error_detail`` so logs match direct ``OpenAI()`` calls.
    """
    out: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "exception_str": str(exc),
    }
    body = getattr(exc, "body", None)
    if body is not None:
        out["exception_body"] = body

    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            out["response_status"] = status
        try:
            text = getattr(response, "text", None)
            if isinstance(text, str) and text.strip():
                frag = text.strip()
                if len(frag) > max_response_text:
                    frag = frag[:max_response_text] + "...(truncated)"
                out["response_text"] = frag
        except Exception as e:
            logger.debug("Exception when extracting response text: %s", e, exc_info=True)

    # Common on openai.APIStatusError / BadRequestError
    for attr in ("request_id", "code", "param", "type"):
        val = getattr(exc, attr, None)
        if val is not None:
            out[f"exception_{attr}"] = val

    return out


def _format_llm_invoke_exception(
    exc: BaseException,
    *,
    max_response_text: int = 16_000,
    max_formatted_len: int = 48_000,
) -> str:
    """JSON matching normal OpenAI client errors: structured body + optional cause chain."""
    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    for _ in range(8):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        chain.append(
            _single_provider_error_detail(cur, max_response_text=max_response_text)
        )
        cur = cur.__cause__

    if not chain:
        payload: dict[str, Any] = {
            "exception_type": type(exc).__name__,
            "exception_str": str(exc),
        }
    elif len(chain) == 1:
        payload = chain[0]
    else:
        payload = {"error_chain": chain}

    raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(raw) > max_formatted_len:
        raw = raw[:max_formatted_len] + "\n...(truncated)"
    return raw


def llm_error_chain_blob(exc: BaseException | None, *, max_depth: int = 8) -> str:
    """Concatenate ``str(exc)`` along ``__cause__`` and the root's ``__context__`` chain.

    Used for substring heuristics (e.g. context-limit phrases) when the wrapper message
    only says "status code is 400".
    """
    if exc is None:
        return ""
    parts: list[str] = []
    seen: set[int] = set()

    def walk_chain(start: BaseException | None) -> None:
        cur = start
        for _ in range(max_depth):
            if cur is None or id(cur) in seen:
                break
            seen.add(id(cur))
            parts.append(str(cur))
            cur = cur.__cause__

    walk_chain(exc)
    ctx = exc.__context__
    if ctx is not None and id(ctx) not in seen:
        walk_chain(ctx)
    return " ".join(parts)


async def _call_model_method(method, primary_kwargs: dict):
    """Invoke sync or async model methods without losing awaitables."""
    if inspect.iscoroutinefunction(method):
        return await method(**primary_kwargs)

    return await asyncio.to_thread(method, **primary_kwargs)


def _extract_usage_payload_from_stream_chunk(raw_chunk: Any) -> Any:
    """从流式原始 chunk 中提取 usage 载荷。

    Args:
        raw_chunk (Any): 模型客户端收到的原始流式 chunk。

    Returns:
        Any: usage 结构；不存在时返回 ``None``。
    """
    if raw_chunk is None:
        return None
    if hasattr(raw_chunk, "usage"):
        usage_value = getattr(raw_chunk, "usage")
        return usage_value if usage_value else None
    if isinstance(raw_chunk, dict):
        usage_value = raw_chunk.get("usage")
        return usage_value if usage_value else None

    decoded = None
    if isinstance(raw_chunk, (bytes, bytearray)):
        decoded = bytes(raw_chunk).decode("utf-8", errors="ignore").strip()
    elif isinstance(raw_chunk, str):
        decoded = raw_chunk.strip()

    if not decoded:
        return None
    if decoded.startswith("data: "):
        decoded = decoded[6:]
    if decoded == "[DONE]":
        return None

    try:
        parsed_payload = json.loads(decoded)
    except Exception:
        return None
    if not isinstance(parsed_payload, dict):
        return None
    usage_value = parsed_payload.get("usage")
    return usage_value if usage_value else None


def _build_usage_only_chunk(raw_chunk: Any, model_name: str) -> AssistantMessageChunk | None:
    """根据 usage-only chunk 构造可合并的 AssistantMessageChunk。

    Args:
        raw_chunk (Any): 原始流式 chunk。
        model_name (str): 模型名称。

    Returns:
        AssistantMessageChunk | None: 可用于累计 usage 的空内容 chunk；无有效 usage 时返回 ``None``。
    """
    usage_payload = _extract_usage_payload_from_stream_chunk(raw_chunk)
    if usage_payload is None:
        return None

    input_tokens, output_tokens, total_tokens = _extract_usage_tokens(usage_payload)
    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return None

    return AssistantMessageChunk(
        content="",
        reasoning_content=None,
        tool_calls=None,
        usage_metadata=UsageMetadata(
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
        finish_reason="null",
    )


def _install_usage_only_chunk_parser(llm_model: Any) -> Any:
    """在共享 client 上安装 usage-only chunk 解析器，并返回恢复函数。

    由于同一个 LLM client 可能被多个协程并发复用，这里使用引用计数保证：
    1. 第一个调用负责安装补偿 parser。
    2. 后续嵌套调用仅增加引用计数，不重复覆盖 parser。
    3. 只有最后一个调用结束时才恢复原始 parser。

    Args:
        llm_model (Any): openjiuwen 模型对象，内部包含 ``_client``。

    Returns:
        Any: 可调用恢复函数；不可安装时返回 ``None``。
    """
    client = getattr(llm_model, "_client", None)
    if client is None:
        return None

    original_parser = getattr(client, "_parse_stream_chunk", None)
    if not callable(original_parser):
        return None

    patch_key = id(client)
    patch_state = _USAGE_ONLY_PARSER_PATCHES.get(patch_key)
    if patch_state is None:
        model_name = getattr(getattr(llm_model, "model_config", None), "model_name", "")

        def _patched_parser(raw_chunk: Any):
            parsed_chunk = original_parser(raw_chunk)
            if parsed_chunk is not None:
                return parsed_chunk
            return _build_usage_only_chunk(raw_chunk, model_name=model_name)

        setattr(client, "_parse_stream_chunk", _patched_parser)
        patch_state = {
            "ref_count": 0,
            "original_parser": original_parser,
            "patched_parser": _patched_parser,
        }
        _USAGE_ONLY_PARSER_PATCHES[patch_key] = patch_state
    patch_state["ref_count"] += 1

    def _restore_parser():
        current_state = _USAGE_ONLY_PARSER_PATCHES.get(patch_key)
        if current_state is None:
            return
        current_state["ref_count"] = max(int(current_state.get("ref_count", 0)) - 1, 0)
        if current_state["ref_count"] > 0:
            return
        try:
            setattr(client, "_parse_stream_chunk", current_state["original_parser"])
        except Exception:
            # 恢复失败不影响主流程，避免掩盖业务异常。
            pass
        finally:
            _USAGE_ONLY_PARSER_PATCHES.pop(patch_key, None)

    return _restore_parser


def _resolve_stream_options(llm_model: Any, need_include_usage: bool) -> dict | None:
    """合并模型已有 stream_options，并按需注入 include_usage。

    Args:
        llm_model (Any): LLM 模型实例，可能包含 model_config.stream_options。
        need_include_usage (bool): 是否强制注入 include_usage。

    Returns:
        dict | None: 合并后的 stream_options；若无可用配置则返回 ``None``。
    """
    merged_options: dict = {}
    model_config = getattr(llm_model, "model_config", None)
    if model_config is not None:
        model_dump = getattr(model_config, "model_dump", None)
        if callable(model_dump):
            model_config_dict = model_dump()
            existed_options = model_config_dict.get("stream_options")
            if isinstance(existed_options, dict):
                merged_options.update(existed_options)

    if need_include_usage:
        merged_options["include_usage"] = True

    return merged_options or None


async def _consume_llm_stream(
    request: _ConsumeLlmStreamRequest,
) -> Any:
    """消费流式 LLM 输出并聚合完整结果。

    Args:
        request: 流消费阶段的具名上下文参数。

    Returns:
        Any: 聚合后的完整响应块。
    """
    full_chunk = None
    async for chunk in request.llm.stream(**request.stream_kwargs):
        _raise_if_cancelled()
        if full_chunk is None:
            full_chunk = chunk
        else:
            full_chunk += chunk
            if len(full_chunk.content) >= MAX_LLM_RESP_LENGTH:
                logger.warning(
                    "[llm_astream] llm response is too long, "
                    "truncate to %s characters", MAX_LLM_RESP_LENGTH
                )
                full_chunk.content = full_chunk.content[:MAX_LLM_RESP_LENGTH]
                break
        chunk_content = getattr(chunk, "content", "")
        if request.can_write_stream and request.need_stream_out and chunk_content:
            payload = {
                "message_id": request.stream_id,
                "agent": request.agent_name,
                "content": chunk_content,
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": StreamEvent.MESSAGE.value,
                "created_time": get_current_time(),
            }
            if request.stream_meta:
                payload.update(dict(request.stream_meta))
            await request.session.write_custom_stream(payload)
    return full_chunk


async def llm_astream(*args, **kwargs):
    """以流式方式调用 LLM 并返回完整响应。

    Args:
        llm (Any): LLM 实例。
        messages (list): LLM 输入消息列表。
        model_name (str): 模型名称。
        agent_name (str): 当前调用方名称，用于流输出元信息。
        tools (Any): 本次调用绑定的工具列表。
        need_stream_out (bool): 是否将增量内容写入会话流。
        stream_meta (dict | None): 附加流事件字段。
        stream_options (dict | None): 传入模型 SDK 的流式配置。

    Returns:
        Any: 聚合后的完整 LLM 响应块。
    """
    llm = kwargs.get("llm", args[0] if len(args) > 0 else None)
    messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
    model_name = kwargs.get("model_name", args[2] if len(args) > 2 else None)
    agent_name = kwargs.get("agent_name", args[3] if len(args) > 3 else None)
    tools = kwargs.get("tools", None)
    need_stream_out = kwargs.get("need_stream_out", False)
    stream_meta = kwargs.get("stream_meta", None)
    stream_options = kwargs.get("stream_options", None)

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

    restore_usage_parser = None
    resolved_timeout = None
    if isinstance(stream_options, dict) and bool(stream_options.get("include_usage")):
        restore_usage_parser = _install_usage_only_chunk_parser(llm)

    try:
        stream_kwargs = {
            "messages": messages,
            "model": model_name,
            "tools": tools,
        }
        if stream_options is not None:
            stream_kwargs["stream_options"] = stream_options

        resolved_timeout = _resolve_agent_llm_timeout(agent_name=agent_name, session=session)
        if resolved_timeout is not None and resolved_timeout.timeout > 0:
            logger.info(
                "[llm_astream] applying wall-clock timeout agent_name=%s "
                "node_key=%s matched_by=%s matched_key=%s timeout=%s",
                agent_name,
                resolved_timeout.resolved_node_key,
                resolved_timeout.matched_by,
                resolved_timeout.matched_key,
                resolved_timeout.timeout,
            )
            full_chunk = await asyncio.wait_for(
                _consume_llm_stream(_ConsumeLlmStreamRequest(
                    llm=llm,
                    stream_kwargs=stream_kwargs,
                    can_write_stream=can_write_stream,
                    need_stream_out=need_stream_out,
                    session=session,
                    stream_id=stream_id,
                    stream_meta=stream_meta,
                    agent_name=agent_name,
                )),
                timeout=resolved_timeout.timeout,
            )
        else:
            full_chunk = await _consume_llm_stream(_ConsumeLlmStreamRequest(
                llm=llm,
                stream_kwargs=stream_kwargs,
                can_write_stream=can_write_stream,
                need_stream_out=need_stream_out,
                session=session,
                stream_id=stream_id,
                stream_meta=stream_meta,
                agent_name=agent_name,
            ))
    except asyncio.TimeoutError as exc:
        if can_write_stream and need_stream_out:
            await session.write_custom_stream(_make_payload(stream_id, StreamEvent.DONE.value, ""))
        logger.warning(
            "[llm_astream] wall-clock timeout agent_name=%s node_key=%s matched_by=%s matched_key=%s timeout=%s",
            agent_name,
            resolved_timeout.resolved_node_key if resolved_timeout else None,
            resolved_timeout.matched_by if resolved_timeout else None,
            resolved_timeout.matched_key if resolved_timeout else None,
            resolved_timeout.timeout if resolved_timeout else None,
        )
        raise CustomValueException(
            error_code=StatusCode.LLM_WALL_CLOCK_TIMEOUT.code,
            message=StatusCode.LLM_WALL_CLOCK_TIMEOUT.errmsg.format(
                timeout=resolved_timeout.timeout,
                agent_name=agent_name,
                matched_by=resolved_timeout.matched_by,
                matched_key=resolved_timeout.matched_key,
                node_key=resolved_timeout.resolved_node_key,
            ),
        ) from exc
    except Exception as e:
        if can_write_stream and need_stream_out:
            await session.write_custom_stream(_make_payload(stream_id, StreamEvent.DONE.value, ""))
        raise e
    finally:
        if callable(restore_usage_parser):
            restore_usage_parser()

    if can_write_stream and need_stream_out:
        await session.write_custom_stream(_make_payload(stream_id, StreamEvent.DONE.value, ""))

    if full_chunk is None:
        logger.error(f"[llm_astream] llm response is None")
        raise CustomValueException(
            error_code=StatusCode.LLM_RESPONSE_NONE.code,
            message=StatusCode.LLM_RESPONSE_NONE.errmsg)
    return full_chunk


def _parse_invoke_llm_args(args, kwargs) -> dict:
    return {
        "llm": kwargs.get("llm", args[0] if len(args) > 0 else None),
        "messages": kwargs.get("messages", args[1] if len(args) > 1 else None),
        "llm_type": kwargs.get("llm_type", "basic"),
        "agent_name": kwargs.get("agent_name", DEFAULT_AGENT_NAME),
        "schema": kwargs.get("schema", None),
        "tools": kwargs.get("tools", None),
        "need_stream_out": kwargs.get("need_stream_out", False),
        "stream_meta": kwargs.get("stream_meta", None),
    }


async def ainvoke_llm_with_stats(*args, **kwargs):
    """调用 LLM 并按配置记录调用统计。

    Args:
        llm (dict): LLM 配置字典，包含 model 与 model_name。
        messages (list): 输入消息列表。
        llm_type (str): LLM 类型标识，默认 "basic"。
        agent_name (str): 调用节点或方法名。
        schema (BaseModel | None): 结构化输出模型；为空时返回统一 dict。
        tools (Any): 本次调用绑定工具。
        need_stream_out (bool): 是否将模型输出写入会话流。
        stream_meta (dict | None): 附加流事件字段。
    Returns:
        dict | BaseModel: schema 不为空时返回 schema 实例，否则返回统一后的 dict。
    """
    invoke_args = _parse_invoke_llm_args(args, kwargs)
    llm = invoke_args["llm"]
    messages = invoke_args["messages"]
    llm_type = invoke_args["llm_type"]
    agent_name = _validate_invoke_agent_name(invoke_args["agent_name"])
    schema = invoke_args["schema"]
    tools = invoke_args["tools"]
    need_stream_out = invoke_args["need_stream_out"]
    stream_meta = invoke_args["stream_meta"]

    _raise_if_cancelled()
    if not llm:
        raise CustomValueException(
            error_code=StatusCode.LLM_INSTANCE_NONE_ERROR.code,
            message=StatusCode.LLM_INSTANCE_NONE_ERROR.errmsg)
    stats_info_llm = _is_llm_stats_enabled()
    session_id = session_id_ctx.get()
    current_session = None
    if stats_info_llm:
        try:
            current_session = session_context.get()
        except Exception:
            current_session = None
        _ensure_workflow_llm_usage_initialized(session_id=session_id, session=current_session)

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
    resolved_stream_options = (
        _resolve_stream_options(llm_model=llm_model, need_include_usage=True)
        if stats_info_llm
        else None
    )

    if agent_name in _DEEPSEARCH_NODE_IDS:
        processed_messages = []
        for m in messages:
            if isinstance(m, (SystemMessage, UserMessage, AssistantMessage, ToolMessage)):
                name = getattr(m, "name", None)
                if name == "" or name is None:
                    msg_dict = m.model_dump()
                    msg_dict.pop("name", None)
                    if isinstance(m, SystemMessage):
                        processed_messages.append(SystemMessage(**msg_dict))
                    elif isinstance(m, UserMessage):
                        processed_messages.append(UserMessage(**msg_dict))
                    elif isinstance(m, AssistantMessage):
                        processed_messages.append(AssistantMessage(**msg_dict))
                    elif isinstance(m, ToolMessage):
                        processed_messages.append(ToolMessage(**msg_dict))
                else:
                    processed_messages.append(m)
            else:
                processed_messages.append(m)
        messages = processed_messages
        invoke_kw = {
            "model": model_name,
            "messages": messages,
            "tools": tools,
        }
        try:
            if hasattr(llm_model, "invoke"):
                response = await _call_model_method(llm_model.invoke, invoke_kw)
            elif hasattr(llm_model, "_ainvoke"):
                ainvoke_method = getattr(llm_model, "_ainvoke", None)
                if ainvoke_method:
                    response = await _call_model_method(ainvoke_method, invoke_kw)
            elif hasattr(llm_model, "ainvoke"):
                response = await _call_model_method(llm_model.ainvoke, invoke_kw)
            else:
                response = await llm_astream(
                    llm=llm_model,
                    messages=messages,
                    model_name=model_name,
                    agent_name=agent_name,
                    tools=tools,
                    need_stream_out=False,
                    stream_meta=stream_meta,
                    stream_options=resolved_stream_options,
                )
        except Exception as e:
            detail = _format_llm_invoke_exception(e)
            corr = format_llm_log_correlation_suffix()
            logger.warning(
                "[ainvoke_llm_with_stats] LLM invoke failed%s agent=%s: %s",
                corr,
                agent_name or "-",
                "*" if LogManager.is_sensitive() else detail,
                exc_info=not LogManager.is_sensitive(),
            )
            raise CustomValueException(
                StatusCode.LLM_CALL_FAILED.code,
                StatusCode.LLM_CALL_FAILED.errmsg.format(e=detail),
            ) from e

    else:
        response = await llm_astream(
            llm=llm_model,
            messages=messages,
            model_name=model_name,
            agent_name=agent_name,
            tools=tools,
            need_stream_out=need_stream_out,
            stream_meta=stream_meta,
            stream_options=resolved_stream_options,
        )

    if stats_info_llm:
        duration = time.time() - start

        # get usage token usage info
        input_tokens, output_tokens, total_tokens = _extract_usage_tokens(response.usage_metadata)

        llm_stat = {
            "method_name": agent_name,
            "duration": duration,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens
        }
        add_workflow_llm_usage(
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            agent_name=agent_name,
        )
        metrics_logger.info(
            f"{TIME_LOGGER_TAG} session_id: {session_id_ctx.get()} ------ [LLM CALL STATISTICS]: {llm_stat}"
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
            # OpenAI Chat Completions requires tool_calls[].type == "function". A previous
            # bug stored "tool_call" here, which causes 400 on the next request when the
            # conversation is replayed.
            new_response.get("tool_calls")[idx]["type"] = "function"
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
                raw_tcs = message.get("tool_calls", []) or []
                fixed_tcs = []
                for tc in raw_tcs:
                    if isinstance(tc, dict):
                        tc = dict(tc)
                        if tc.get("type") == "tool_call":
                            tc["type"] = "function"
                        elif not tc.get("type"):
                            tc["type"] = "function"
                        fixed_tcs.append(tc)
                    else:
                        fixed_tcs.append(tc)
                output_messages.append(
                    AssistantMessage(
                        content=content,
                        name=name,
                        tool_calls=fixed_tcs,
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

    # 部分模型不支持仅传入 system message，缺少 user message 时补一个低语义占位消息兜底。
    if not any(isinstance(message, UserMessage) for message in output_messages):
        output_messages.append(UserMessage(content="."))

    return output_messages


def record_llm_retry_log(*args, **kwargs):
    """Record the retry log of LLM."""
    current_try = kwargs.get("current_try", args[0] if len(args) > 0 else 0)
    max_retries = kwargs.get("max_retries", args[1] if len(args) > 1 else 3)
    section_idx = kwargs.get("section_idx", args[2] if len(args) > 2 else None)
    step_title = kwargs.get("step_title", args[3] if len(args) > 3 else None)
    operation = kwargs.get("operation", args[4] if len(args) > 4 else None)
    error = kwargs.get("error", args[5] if len(args) > 5 else None)
    extra_info = kwargs.get("extra_info", args[6] if len(args) > 6 else None)
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
