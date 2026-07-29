# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import json
from pathlib import Path
from typing import Optional

from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler


def setup_interface_logger(
        log_dir: Optional[str] = None,
        level=logging.INFO,
        max_bytes: int = 100 * 1024 * 1024,
        backup_count: int = 20
):
    """
    初始化接口日志 logger
    """
    logger = logging.getLogger("deepsearch_interface")
    logger.propagate = False
    logger.setLevel(level)

    if logger.handlers:
        for handler in list(logger.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception as e:
                logging.getLogger().info(f"Error closing handler: {e}")
        logger.handlers.clear()

    if log_dir is None:
        handler = logging.StreamHandler()
    else:
        log_dir_path = Path(log_dir)
        interface_log_dir = log_dir_path / "interface"
        interface_log_path = interface_log_dir / "deepsearch_interface.log"
        handler = SafeRotatingFileHandler(
            filename=str(interface_log_path),
            mode='a',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

    formatter = logging.Formatter("%(asctime)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def record_interface_log(*args, **kwargs):
    role = kwargs.get("role", args[0] if len(args) > 0 else "")
    session_id = kwargs.get("session_id", args[1] if len(args) > 1 else "")
    api_name = kwargs.get("api_name", args[2] if len(args) > 2 else "")
    duration_min = kwargs.get("duration_min", args[3] if len(args) > 3 else 0.0)
    success = kwargs.get("success", args[4] if len(args) > 4 else False)
    response_info = kwargs.get("response_info", args[5] if len(args) > 5 else {})
    """
    记录接口日志
    """
    interface_logger = logging.getLogger("deepsearch_interface")
    result = "success" if success else "fail"
    response_str = json.dumps(response_info, ensure_ascii=False)
    interface_logger.info(f"{role} | {session_id} | {api_name} | {duration_min:.2f} | {result} | {response_str}")
