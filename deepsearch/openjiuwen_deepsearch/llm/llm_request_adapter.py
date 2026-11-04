# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import logging
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from openjiuwen_deepsearch.config.config import Config, LLMConfig

logger = logging.getLogger(__name__)

_THINKING_TYPE_PROVIDER_KEYWORDS = (
    "deepseek",
    "bigmodel",
    "zhipu",
    "kimi",
    "moonshot",
    "volces",
    "ark",
)


@dataclass(frozen=True)
class ThinkingRule:
    name: str
    match: Callable[[LLMConfig], bool]
    apply: Optional[Callable[[dict, bool, LLMConfig], None]]
    supported: bool = True


def _config_text(config: LLMConfig) -> str:
    return f"{config.base_url} {config.model_name}".lower()


def _url_path(config: LLMConfig) -> str:
    return urlparse(config.base_url or "").path.lower()


def _url_path_segments(config: LLMConfig) -> list[str]:
    return [segment for segment in _url_path(config).split("/") if segment]


def _has_path_sequence(segments: list[str], *expected: str) -> bool:
    if not expected:
        return False
    expected_segments = list(expected)
    return any(
        segments[index:index + len(expected_segments)] == expected_segments
        for index in range(len(segments) - len(expected_segments) + 1)
    )


def _is_siliconflow(config: LLMConfig) -> bool:
    return config.model_type == "siliconflow"


def _is_minimax(config: LLMConfig) -> bool:
    return "minimax" in _config_text(config)


def _is_dashscope(config: LLMConfig) -> bool:
    base_url = (config.base_url or "").lower()
    return any(key in base_url for key in ("dashscope", "bailian", "aliyuncs"))


def _is_huawei_maas(config: LLMConfig) -> bool:
    text = _config_text(config)
    return any(key in text for key in ("huawei", "huaweicloud", "maas", "modelarts"))


def _is_huawei_maas_openai_compatible(config: LLMConfig) -> bool:
    segments = _url_path_segments(config)
    return _is_huawei_maas(config) and _has_path_sequence(segments, "openai", "v1")


def _is_huawei_maas_standard_v1(config: LLMConfig) -> bool:
    segments = _url_path_segments(config)
    return (
        _is_huawei_maas(config)
        and not _is_huawei_maas_openai_compatible(config)
        and "v1" in segments
    )


def _is_huawei_maas_standard_v2(config: LLMConfig) -> bool:
    segments = _url_path_segments(config)
    return _is_huawei_maas(config) and "v2" in segments


def _is_thinking_type_provider(config: LLMConfig) -> bool:
    text = _config_text(config)
    for keyword in _THINKING_TYPE_PROVIDER_KEYWORDS:
        if keyword in text:
            return True
    return False


def _apply_extra_body_thinking_type(extension: dict, enabled: bool, _: LLMConfig) -> None:
    extra_body = extension.setdefault("extra_body", {})
    extra_body["thinking"] = {"type": "enabled" if enabled else "disabled"}


def _apply_extra_body_enable_thinking(extension: dict, enabled: bool, _: LLMConfig) -> None:
    extra_body = extension.setdefault("extra_body", {})
    extra_body["enable_thinking"] = enabled


def _apply_top_level_enable_thinking(extension: dict, enabled: bool, _: LLMConfig) -> None:
    extension["enable_thinking"] = enabled


def _apply_chat_template_kwargs(extension: dict, enabled: bool, _: LLMConfig) -> None:
    extra_body = extension.setdefault("extra_body", {})
    chat_template_kwargs = extra_body.setdefault("chat_template_kwargs", {})

    # 华为 MaaS OpenAI 兼容接口使用 chat_template_kwargs.enable_thinking 控制思考模式。
    chat_template_kwargs["enable_thinking"] = enabled


THINKING_RULES = (
    # 规则按“越具体越靠前”的顺序匹配，避免通用规则误覆盖厂商专属传参方式。
    # 例如 SiliconFlow 可能承载 DeepSeek/GLM/Qwen 等模型，但其开关字段是顶层
    # enable_thinking；华为 MaaS 标准 v1/v2 与 OpenAI 兼容接口需要先按 URL
    # 路径区分，再决定使用 extra_body.thinking.type 或 chat_template_kwargs.enable_thinking。
    # thinking_type 是兜底规则，只处理 DeepSeek、智谱、Kimi、火山等通用
    # {"thinking": {"type": ...}} 形态，必须放在厂商专属规则之后。
    ThinkingRule("siliconflow", _is_siliconflow, _apply_top_level_enable_thinking),
    ThinkingRule("minimax", _is_minimax, None, supported=False),
    ThinkingRule("dashscope", _is_dashscope, _apply_extra_body_enable_thinking),
    ThinkingRule(
        "huawei_maas_openai_compatible",
        _is_huawei_maas_openai_compatible,
        _apply_chat_template_kwargs,
    ),
    ThinkingRule(
        "huawei_maas_standard_v1",
        _is_huawei_maas_standard_v1,
        _apply_extra_body_thinking_type,
    ),
    ThinkingRule(
        "huawei_maas_standard_v2",
        _is_huawei_maas_standard_v2,
        _apply_extra_body_thinking_type,
    ),
    ThinkingRule("thinking_type", _is_thinking_type_provider, _apply_extra_body_thinking_type),
)


def _select_thinking_rule(config: LLMConfig) -> Optional[ThinkingRule]:
    return next((rule for rule in THINKING_RULES if rule.match(config)), None)


def _remove_existing_thinking_fields(extension: dict) -> list[str]:
    """清理旧思考参数，避免同一次请求中出现多个互相冲突的开关字段。"""
    removed_fields = []
    if "enable_thinking" in extension:
        removed_fields.append("enable_thinking")
        extension.pop("enable_thinking", None)

    extra_body = extension.get("extra_body")
    if not isinstance(extra_body, dict):
        return removed_fields

    if "thinking" in extra_body:
        removed_fields.append("extra_body.thinking")
        extra_body.pop("thinking", None)
    if "enable_thinking" in extra_body:
        removed_fields.append("extra_body.enable_thinking")
        extra_body.pop("enable_thinking", None)

    chat_template_kwargs = extra_body.get("chat_template_kwargs")
    if not isinstance(chat_template_kwargs, dict):
        return removed_fields

    if "thinking" in chat_template_kwargs:
        removed_fields.append("extra_body.chat_template_kwargs.thinking")
        chat_template_kwargs.pop("thinking", None)
    if "enable_thinking" in chat_template_kwargs:
        removed_fields.append("extra_body.chat_template_kwargs.enable_thinking")
        chat_template_kwargs.pop("enable_thinking", None)
    if not chat_template_kwargs:
        extra_body.pop("chat_template_kwargs", None)
    return removed_fields


def _list_existing_thinking_fields(extension: dict) -> list[str]:
    """列出 extension 中已有的思考开关字段路径，不修改传入对象。"""
    fields = []
    if "enable_thinking" in extension:
        fields.append("enable_thinking")

    extra_body = extension.get("extra_body")
    if not isinstance(extra_body, dict):
        return fields

    if "thinking" in extra_body:
        fields.append("extra_body.thinking")
    if "enable_thinking" in extra_body:
        fields.append("extra_body.enable_thinking")

    chat_template_kwargs = extra_body.get("chat_template_kwargs")
    if not isinstance(chat_template_kwargs, dict):
        return fields

    if "thinking" in chat_template_kwargs:
        fields.append("extra_body.chat_template_kwargs.thinking")
    if "enable_thinking" in chat_template_kwargs:
        fields.append("extra_body.chat_template_kwargs.enable_thinking")
    return fields


def _prune_empty_thinking_containers(extension: dict) -> None:
    """删除 fallback 清理思考字段后留下的空容器。"""
    extra_body = extension.get("extra_body")
    if not isinstance(extra_body, dict):
        return

    chat_template_kwargs = extra_body.get("chat_template_kwargs")
    if isinstance(chat_template_kwargs, dict) and not chat_template_kwargs:
        extra_body.pop("chat_template_kwargs", None)
    if not extra_body:
        extension.pop("extra_body", None)


def _build_rule_injected_thinking_fields(llm_config: LLMConfig, rule: ThinkingRule) -> list[str]:
    """计算当前厂商规则在主请求中会注入的思考开关字段。"""
    generated_extension = {}
    rule.apply(generated_extension, False, llm_config)
    return _list_existing_thinking_fields(generated_extension)


def build_thinking_fallback_extension(llm_config: LLMConfig, extension: dict) -> tuple[dict, list[str]]:
    """构造移除思考开关后的 fallback extension，仅对已支持的厂商规则生效。

    ``extension`` 应是 LLMConfig 中的原始 extension，而不是已经注入
    ``thinking_enabled=False`` 的主请求 extension。fallback 只继承用户原始
    extension 中与思考开关无关的字段，避免把主请求临时注入的字段泄漏到
    fallback model。
    """
    fallback_extension = copy.deepcopy(extension or {})
    rule = _select_thinking_rule(llm_config)
    if rule is None or not rule.supported or rule.apply is None:
        return fallback_extension, []
    removed_fields = _remove_existing_thinking_fields(fallback_extension)
    _prune_empty_thinking_containers(fallback_extension)

    for field in _build_rule_injected_thinking_fields(llm_config, rule):
        if field not in removed_fields:
            removed_fields.append(field)
    return fallback_extension, removed_fields


def merge_thinking_extension(llm_config: LLMConfig, thinking_enabled: bool) -> dict:
    """按厂商规则把 SDK 内部思考开关合并到 extension，不修改原始 LLMConfig。"""
    extension = copy.deepcopy(llm_config.extension or {})
    rule = _select_thinking_rule(llm_config)
    if rule is None or not rule.supported or rule.apply is None:
        logger.warning(
            "Model does not support thinking switch, skip applying thinking_enabled=%s, "
            "model=%s, base_url=%s, rule=%s",
            thinking_enabled,
            llm_config.model_name,
            llm_config.base_url,
            rule.name if rule else "unknown",
        )
        return extension

    # 确认厂商规则后再清理旧字段，未知厂商的原有 extension 保持不变。
    removed_fields = _remove_existing_thinking_fields(extension)
    if removed_fields:
        logger.warning(
            "Existing thinking fields in LLMConfig.extension are overridden by "
            "llm_thinking_enabled=%s, model=%s, base_url=%s, rule=%s, fields=%s",
            thinking_enabled,
            llm_config.model_name,
            llm_config.base_url,
            rule.name,
            removed_fields,
        )
    rule.apply(extension, thinking_enabled, llm_config)
    return extension


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def resolve_llm_thinking_enabled(service_config: Optional[dict] = None) -> bool:
    """解析运行时思考开关；未显式传入时使用 SDK 默认配置。"""
    if hasattr(service_config, "model_dump"):
        service_config = service_config.model_dump()
    if isinstance(service_config, dict) and "llm_thinking_enabled" in service_config:
        return _coerce_bool(service_config.get("llm_thinking_enabled"))
    return Config().service_config.llm_thinking_enabled
