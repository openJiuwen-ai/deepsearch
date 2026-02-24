# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import time
from functools import wraps
from pathlib import Path
from typing import Optional

from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler

metrics_logger = logging.getLogger("metrics")

TIME_LOGGER_TAG = "[TIME_STATS]"
ENABLE_NODE_DURATION_STATS = Config().service_config.stats_info_node_duration


def setup_metrics_logger(
        log_dir: Optional[str] = None,
        level=logging.INFO,
        max_bytes: int = 100 * 1024 * 1024,  # 100 MB
        backup_count: int = 5,
        is_sensitive: bool = True
):
    """初始化性能打点日志的logger."""
    metrics = logging.getLogger("metrics")
    metrics.propagate = False
    metrics.setLevel(level)

    if metrics.handlers:
        for handler in list(metrics.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception as e:
                # Log the exception
                if not is_sensitive:
                    logging.getLogger().info(f"Error closing handler: {e}")
                else:
                    logging.getLogger().info(f"Error closing handler")
        metrics.handlers.clear()

    # 根据 log_dir 决定输出方式
    if log_dir is None:
        handler = logging.StreamHandler()
    else:
        log_dir_path = Path(log_dir)
        metrics_dir = log_dir_path / "metrics"
        metrics_log_path = metrics_dir / "metrics.log"
        handler = SafeRotatingFileHandler(
            filename=str(metrics_log_path),
            mode='a',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

    formatter = logging.Formatter("%(asctime)s - %(message)s")
    handler.setFormatter(formatter)
    metrics.addHandler(handler)


def async_time_logger(method_name):
    """异步计时器."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not ENABLE_NODE_DURATION_STATS:
                return await func(*args, **kwargs)

            # get thread_id(session_id)
            session = kwargs.get("session") if "session" in kwargs else (args[2] if len(args) > 2 else None)
            thread_id = session.get_global_state("config.thread_id") or "default_session_id"
            section_idx = session.get_global_state("search_context.section_idx")

            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                class_name = type(args[0]).__name__ if args else "UnknownClass"
                metrics_logger.info(
                    f"{TIME_LOGGER_TAG} thread_id: {thread_id} ------ [{class_name}"
                    f"{f'[{section_idx}]' if section_idx is not None else ''}.{method_name}] "
                    f"executed time: {duration:.2f} s")

        return wrapper

    return decorator
