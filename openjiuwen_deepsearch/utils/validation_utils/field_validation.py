# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.text_utils import validate_string_length
from openjiuwen_deepsearch.common.common_constants import MAX_QUERY_LENGTH


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
