# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import time
from functools import wraps
from pathlib import Path
from typing import Optional

from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.log_utils.log_common import (
    RunIdFilter,
    RotationConfig,
    RunPrefix,
)
from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler

metrics_logger = logging.getLogger("metrics")

TIME_LOGGER_TAG = "[TIME_STATS]"
ENABLE_NODE_DURATION_STATS = Config().service_config.stats_info_node_duration

_METRICS_FORMATTER = logging.Formatter("%(asctime)s - %(message)s")


def _create_metrics_file_handler(
        log_dir_path: Path,
        run_prefix: RunPrefix,
        rotation: RotationConfig,
        level: int = logging.INFO,
        run_id: Optional[str] = None,
) -> logging.Handler:
    """创建 metrics 文件 handler (公共逻辑)。

    init (run_id=None) 和 per-run (run_id 非空) 共用此函数,
    per-run 时额外添加 RunIdFilter 实现隔离。

    Args:
        log_dir_path: 日志根目录
        run_prefix: 运行标识前缀 (date_str + run_prefix)
        rotation: 文件轮转配置
        level: 日志级别
        run_id: 非空时添加 RunIdFilter (per-run), None 时不添加 (init)

    Returns:
        metrics_handler
    """
    metrics_dir = log_dir_path / "metrics" / run_prefix.date_str
    metrics_log_path = metrics_dir / f"metrics_{run_prefix.run_prefix}.log"
    handler = SafeRotatingFileHandler(
        filename=str(metrics_log_path),
        mode='a',
        maxBytes=rotation.max_bytes,
        backupCount=rotation.backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(_METRICS_FORMATTER)
    handler.setLevel(level)
    if run_id is not None:
        handler.addFilter(RunIdFilter(run_id))
    return handler


def setup_metrics_logger(
        log_dir: Optional[str] = None,
        level=logging.INFO,
        rotation: RotationConfig = RotationConfig(),
        is_sensitive: bool = True,
        run_prefix: RunPrefix = RunPrefix(),
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
        handler = _create_metrics_file_handler(
            log_dir_path=log_dir_path,
            run_prefix=run_prefix,
            rotation=rotation,
            level=level,
        )

    metrics.addHandler(handler)


def create_per_run_metrics_handler(
        log_dir: str,
        run_id: str,
        level: int = logging.INFO,
        rotation: RotationConfig = RotationConfig(),
        run_prefix: RunPrefix = RunPrefix(),
) -> list[logging.Handler]:
    """创建 per-run metrics handler,通过 RunIdFilter 隔离。

    返回 handler 列表,由调用方 (LogManager.new_run) 添加到 metrics logger。
    每个 handler 只捕获 run_id_ctx 匹配的日志记录。
    调用方需在结束时调用 LogManager.end_run(run_id) 清理。

    Args:
        log_dir: 日志根目录
        run_id: 本次运行的唯一标识
        level: 日志级别
        rotation: 文件轮转配置
        run_prefix: 运行标识前缀 (与 common 共享)

    Returns:
        handlers: 创建的 handler 列表 (metrics)
    """
    log_dir_path = Path(log_dir)
    handler = _create_metrics_file_handler(
        log_dir_path=log_dir_path,
        run_prefix=run_prefix,
        rotation=rotation,
        level=level,
        run_id=run_id,
    )
    return [handler]


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
