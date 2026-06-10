# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import hashlib
import json
import logging
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_model_factory import LLMModelFactory, LLMModelParams
from openjiuwen_deepsearch.config.config import Config, LLMConfig
from openjiuwen_deepsearch.llm.llm_request_adapter import build_thinking_fallback_extension, merge_thinking_extension
from openjiuwen_deepsearch.utils.common_utils.security_utils import zero_secret
from openjiuwen_deepsearch.utils.validation_utils.param_validation import validate_llm_obj_params

logger = logging.getLogger(__name__)


def _normalize_thinking_fallback_base_url(base_url: str | None) -> str:
    raw_base_url = (base_url or "").strip()
    if not raw_base_url:
        return ""

    parts = urlsplit(raw_base_url)
    if not parts.scheme or not parts.netloc:
        return raw_base_url.rstrip("/")

    normalized_path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), normalized_path, parts.query, ""))


def _build_thinking_fallback_key(llm_config: LLMConfig) -> str:
    payload = {
        "model_type": (llm_config.model_type or "").strip().lower(),
        "base_url": _normalize_thinking_fallback_base_url(llm_config.base_url),
        "model_name": (llm_config.model_name or "").strip(),
    }
    encoded_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest()
    return f"thinking_fallback:v1:{digest}"


def create_llm_obj(llm_config: LLMConfig, thinking_enabled: Optional[bool] = None):
    """创建自定义llm"""
    try:
        validate_llm_obj_params(llm_config)
        # thinking_enabled 是三态语义：None 表示保持原始 extension，不注入思考开关；
        # True/False 表示按厂商规则显式开启/关闭思考模式。
        merged_extension = (
            llm_config.extension
            if thinking_enabled is None
            else merge_thinking_extension(llm_config, thinking_enabled)
        )
        api_key = bytes(llm_config.api_key).decode('utf-8')
        factory = LLMModelFactory()
        model_params = LLMModelParams(
            model_provider=llm_config.model_type,
            api_key=api_key,
            api_base=llm_config.base_url,
            timeout=Config().service_config.llm_timeout,
            hyper_parameters=llm_config.hyper_parameters,
            extension=merged_extension
        )
        model = factory.get_model(model_params)
        fallback_model = None
        fallback_removed_fields = []
        if thinking_enabled is False:
            fallback_extension, fallback_removed_fields = build_thinking_fallback_extension(
                llm_config, llm_config.extension
            )
            if fallback_removed_fields:
                fallback_params = LLMModelParams(
                    model_provider=llm_config.model_type,
                    api_key=api_key,
                    api_base=llm_config.base_url,
                    timeout=Config().service_config.llm_timeout,
                    hyper_parameters=llm_config.hyper_parameters,
                    extension=fallback_extension
                )
                fallback_model = factory.get_model(fallback_params)
        model_name = llm_config.model_name
        return dict(
            model=model,
            model_name=model_name,
            thinking_fallback_model=fallback_model,
            thinking_fallback_key=_build_thinking_fallback_key(llm_config),
            thinking_fallback_removed_fields=fallback_removed_fields,
            thinking_fallback_active=False,
        )
    finally:
        zero_secret(llm_config.api_key)
