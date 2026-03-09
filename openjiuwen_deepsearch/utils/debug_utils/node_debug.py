# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import datetime
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NODE_DEBUG_LOGGER
from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler

NODE_DEBUG_ENABLE = Config().service_config.node_debug_enable


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


@dataclass
class NodeDebugLogRecord:
    pre_node: str
    cur_node: str
    message_id: str
    log_type: str
    node_type: str
    content: str


@dataclass
class NodeDebugData:
    node_name: str
    msg_id: str
    node_type: str
    input_content: str = ""
    output_content: str = ""


def _record_node_debug_log(record: NodeDebugLogRecord):
    """
        工作流节点记录格式化 debug 日志
    """
    log_str = json.dumps(
        {
            "pre_node": record.pre_node,
            "cur_node": record.cur_node,
            "message_id": record.message_id,
            "type": record.log_type,
            "timestamp": str(time.time()),
            "node_type": record.node_type,
            "content": record.content
        },
        ensure_ascii=False,
    )

    debug_logger = logging.getLogger(NODE_DEBUG_LOGGER)
    debug_logger.debug(log_str)


def add_debug_log_wrapper(session, debug_data: NodeDebugData):
    """
        工作流节点添加格式化 debug 日志 wrapper
    """
    if not NODE_DEBUG_ENABLE:
        return

    pre_node = session.get_global_state("search_context.debug_pre_node") or ""
    cur_node = f"{debug_data.node_name}-{uuid.uuid4()}"

    if debug_data.input_content:
        _record_node_debug_log(NodeDebugLogRecord(
            pre_node, cur_node, debug_data.msg_id, LogType.INPUT.value, debug_data.node_type, debug_data.input_content
        ))

    if debug_data.output_content:
        _record_node_debug_log(NodeDebugLogRecord(
            pre_node, cur_node, debug_data.msg_id, LogType.OUTPUT.value, debug_data.node_type, debug_data.output_content
        ))

    session.update_global_state({"search_context.debug_pre_node": cur_node})


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
