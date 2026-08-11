# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.text_utils import validate_string_length
from openjiuwen_deepsearch.common.common_constants import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)


def validate_str_field(field_name: str, value, max_len=MAX_QUERY_LENGTH) -> None:
    '''
    校验参数字段为String类型和长度
    '''
    if not isinstance(value, str):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_STRING.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_STRING.errmsg.format(field=field_name)
        )
    if not validate_string_length(value, max_length=max_len):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_STRING_LENGTH.code,
            StatusCode.PARAM_CHECK_ERROR_STRING_LENGTH.errmsg.format(field=field_name)
        )


def validate_bytearray_field(field_name: str, value) -> None:
    '''
    校验参数字段为bytearray类型
    '''
    if not isinstance(value, bytearray):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_BYTEARRAY.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_BYTEARRAY.errmsg.format(field=field_name)
        )


def validate_bool_field(field_name: str, value) -> None:
    '''
    校验参数字段为bool类型
    '''
    if not isinstance(value, bool):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_BOOL.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_BOOL.errmsg.format(field=field_name)
        )


def validate_not_empty_field(field_name: str, value) -> None:
    '''
    校验参数字段不为空
    '''
    if not value or (isinstance(value, str) and not value.strip()):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.errmsg.format(field=field_name)
        )


def validate_required_field(field_name: str, data: dict) -> None:
    '''
    校验dict中存在某字段，并且不为None
    '''
    if field_name not in data:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_EXIST.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_EXIST.errmsg.format(field=field_name)
        )

    if data[field_name] is None:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.errmsg.format(field=field_name)
        )


def validate_agent_required_field(data: dict) -> None:
    '''
    校验agent_config中的必填字段
    '''
    if data is None:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.errmsg.format(field="agent_config")
        )
    validate_required_field("execute_mode", data)
    validate_required_field("llm_config", data)
    validate_required_field("info_collector_search_method", data)
    web_search = data.get("web_search_engine_config")
    local_search = data.get("local_search_engine_config")
    if not (web_search or local_search):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.code,
            StatusCode.PARAM_CHECK_ERROR_FIELD_EMPTY.errmsg.format(field="search_engine_config")
        )


def validate_vlm_chart_generator_field(data: dict) -> None:
    """校验 VLM 图表生成器配置字段是否合法。"""

    vlm_chart_generator_enable = data.get("vlm_chart_generator_enable", False)
    vlm_chart_generator_max_iterations = data.get("vlm_chart_generator_max_iterations", 1)
    vlm_model_config = data.get("llm_config", {}).get("vlm_chart_generating", {})

    # 解析多模态llm配置
    if vlm_chart_generator_enable:
        # vlm迭代轮次大于0, 必须传入vlm模型相关配置
        if vlm_chart_generator_max_iterations > 0:
            model_config = [
                vlm_model_config.get("model_name", ""),
                vlm_model_config.get("model_type", ""),
                vlm_model_config.get("base_url", ""),
                vlm_model_config.get("api_key", ""),
            ]
            if not all(model_config):
                data["vlm_chart_generator_enable"] = False
                data["vlm_chart_generator_max_iterations"] = 0
                logger.warning("开启vlm迭代生成图开关且vlm迭代轮次大于0时，\
                               必须提供 vlm_model_name、type、base_url 和 api_key， 当前vlm迭代生成图开关已关闭。")
