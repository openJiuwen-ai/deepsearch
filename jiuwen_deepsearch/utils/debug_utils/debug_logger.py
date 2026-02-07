# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
This script is used to print debug message
"""
import datetime
import enum
import json
import logging
import os
import time
import uuid
from pathlib import Path

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.config.config import Config

debug_logger = None
debug_enable = Config().service_config.model_dump().get("debug_enable", False)
DEFAULT_DEBUG_LOG_DIR = "./logs/debug_log"


def validate_and_normalize_path(path: str):
    """
    日志路径校验
    """
    if not path:
        return None
    try:
        target = Path(path).resolve()
    except Exception as ex:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_LOG_DIR_INVALID.code,
            message="Invalid log directory.",
        ) from ex
    safe_base = Path(DEFAULT_DEBUG_LOG_DIR).resolve()

    try:
        target.relative_to(safe_base)
    except ValueError as ex:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_LOG_DIR_UNSAFE.code,
            message="Unsafe log directory.",
        ) from ex

    return str(target)


if debug_enable:
    debug_logger = logging.getLogger("debug_logger")
    debug_logger.setLevel(logging.DEBUG)
    debug_logger.propagate = False

    debug_file_dir = Config().service_config.model_dump().get("debug_log_file_dir", DEFAULT_DEBUG_LOG_DIR)
    # 路径标准化与合法性校验
    try:
        debug_file_dir = validate_and_normalize_path(debug_file_dir)
    except Exception as e:
        logging.warning(f"debug log file dir is invalid, use default log dir.")
        debug_file_dir = Path(DEFAULT_DEBUG_LOG_DIR).resolve()

    os.makedirs(debug_file_dir, exist_ok=True)
    app_handler = logging.FileHandler(
        os.path.join(debug_file_dir,
                     f'debug_{datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")}.log'),
        encoding='utf-8'
    )
    app_handler.setLevel(logging.DEBUG)
    app_formatter = logging.Formatter('%(message)s')
    app_handler.setFormatter(app_formatter)
    debug_logger.addHandler(app_handler)


def add_debug_log(pre_step, cur_step, message_id, log_type, node_type, content):
    """
        debug 日志格式化打印函数
    """
    if not debug_enable:
        return
    if not isinstance(cur_step, str):
        content = "Add debug log failed for invalid parameter type. Required data type is string."

    log_str = json.dumps(
        {
            "pre_step": pre_step,
            "cur_step": cur_step,
            "message_id": message_id,
            "type": log_type,
            "timestamp": str(time.time()),
            "node_type": node_type,
            "content": content
        },
        ensure_ascii=False,
    )
    debug_logger.debug(log_str)


def add_debug_log_wrapper(runtime, node_name, msg_id, node_type, input_content="", output_content=""):
    """
        debug 日志记录包装方法
    """
    if not debug_enable:
        return
    debug_pre_step = runtime.get_global_state("search_context.debug_pre_step") or ""
    cur_step = runtime.get_global_state("search_context.debug_cur_step") or ""
    # 判断是否一个step内第一次执行, 如果是则新建一个cur_step
    if not cur_step.startswith(node_name):
        cur_step = f"{node_name}-{uuid.uuid4()}"
        runtime.update_global_state({"search_context.debug_cur_step": cur_step})
    if input_content:
        add_debug_log(debug_pre_step, cur_step, msg_id, LogType.INPUT.value, node_type, input_content)
    if output_content:
        add_debug_log(debug_pre_step, cur_step, msg_id, LogType.OUTPUT.value, node_type, output_content)
    runtime.update_global_state({"search_context.debug_pre_step": cur_step})


class NodeType(enum.Enum):
    MAIN = "main"
    SUB = "sub"


class LogType(enum.Enum):
    INPUT = "input"
    OUTPUT = "output"
