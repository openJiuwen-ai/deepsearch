# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.utils.validation_utils.field_validation import validate_not_empty_field, \
    validate_str_field, validate_bool_field, validate_bytearray_field


def validate_generate_template_params(
        file_name: str,
        file_stream: str,
        is_template,
):
    """
    校验 generate template 入参
    """
    validate_not_empty_field("file_name", file_name)
    validate_str_field("file_name", file_name)

    validate_not_empty_field("file_stream", file_stream)
    validate_str_field("file_stream", file_stream)

    validate_bool_field("is_template", is_template)


def validate_run_agent_params(
        message: str,
        conversation_id: str,
        report_template: str = "",
        interrupt_feedback: str = "",
):
    """
    校验 run agent 入参
    """
    validate_not_empty_field("message", message)
    validate_str_field("message", message)

    validate_not_empty_field("conversation_id", conversation_id)
    validate_str_field("conversation_id", conversation_id)

    validate_str_field("report_template", report_template)

    validate_str_field("interrupt_feedback", interrupt_feedback)

    if interrupt_feedback.strip() and interrupt_feedback not in ("", "accepted"):
        raise CustomValueException(StatusCode.PARAM_CHECK_ERROR_INTERRUPT_FEEDBACK_ERROR.code,
                                   StatusCode.PARAM_CHECK_ERROR_INTERRUPT_FEEDBACK_ERROR.errmsg)


def validate_llm_obj_params(input_config: LLMConfig):
    """
    校验自定义的 llm 入参
    """
    llm_config = input_config.model_dump()
    model_provider = llm_config.get("model_type", "")
    api_key = llm_config.get("api_key", "")
    api_base = llm_config.get("base_url", "")
    model_name = llm_config.get("model_name", "")

    validate_not_empty_field("model_provider", model_provider)
    validate_str_field("model_provider", model_provider)

    validate_not_empty_field("api_key", api_key)
    validate_bytearray_field("api_key", api_key)

    validate_not_empty_field("api_base", api_base)
    validate_str_field("api_base", api_base)

    validate_not_empty_field("model_name", model_name)
    validate_str_field("model_name", model_name)
