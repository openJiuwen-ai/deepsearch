# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import datetime
import enum
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from jiuwen_deepsearch.utils.constants_utils.node_constants import NODE_DEBUG_LOGGER
from jiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler


def setup_debug_logger(
        name: str = None,
        log_dir: Optional[str] = None,
        max_bytes: int = 100 * 1024 * 1024,
        backup_count: int = 20,
        is_sensitive: bool = True
):
    """
        初始化 debug 日志 logger
    """
    debug_logger = logging.getLogger(name)
    debug_logger.setLevel(logging.DEBUG)
    debug_logger.propagate = False

    if debug_logger.handlers:
        for handler in list(debug_logger.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception as e:
                if not is_sensitive:
                    debug_logger.info(f"Error closing handler: {e}")
                else:
                    debug_logger.info(f"Error closing handler.")
        debug_logger.handlers.clear()

    if log_dir is None:
        handler = logging.StreamHandler()
    else:
        log_dir_path = Path(log_dir)
        interface_log_dir = log_dir_path / "node_debug_log"
        interface_log_path = (
                interface_log_dir /
                f"node_debug_{datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
        )
        handler = SafeRotatingFileHandler(
            filename=str(interface_log_path),
            mode='a',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

    handler.setLevel(logging.DEBUG)
    app_formatter = logging.Formatter('%(message)s')
    handler.setFormatter(app_formatter)
    debug_logger.addHandler(handler)


def record_node_debug_log(pre_node, cur_node, message_id, log_type, node_type, content):
    """
        工作流节点记录格式化 debug 日志
    """
    log_str = json.dumps(
        {
            "pre_node": pre_node,
            "cur_node": cur_node,
            "message_id": message_id,
            "type": log_type,
            "timestamp": str(time.time()),
            "node_type": node_type,
            "content": content
        },
        ensure_ascii=False,
    )

    debug_logger = logging.getLogger(NODE_DEBUG_LOGGER)
    debug_logger.debug(log_str)


def add_debug_log_wrapper(runtime, node_name, msg_id, node_type, input_content="", output_content=""):
    """
        工作流节点添加格式化 debug 日志 wrapper
    """
    pre_node = runtime.get_global_state("search_context.debug_pre_node") or ""
    cur_node = f"{node_name}-{uuid.uuid4()}"

    if input_content:
        record_node_debug_log(pre_node, cur_node, msg_id, LogType.INPUT.value, node_type, input_content)

    if output_content:
        record_node_debug_log(pre_node, cur_node, msg_id, LogType.OUTPUT.value, node_type, output_content)

    runtime.update_global_state({"search_context.debug_pre_node": cur_node})


class NodeType(enum.Enum):
    """
        workflow level
    """
    MAIN = "main"
    SUB = "sub"


class LogType(enum.Enum):
    """
        log data type
    """
    INPUT = "input"
    OUTPUT = "output"
