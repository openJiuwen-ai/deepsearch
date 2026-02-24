# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import contextvars
import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler

# ContextVar for per-request session_id
session_id_ctx = contextvars.ContextVar("session_id", default="-")


class SessionFilter(logging.Filter):
    """Injects session_id into every log record."""

    def filter(self, record):
        """session filter"""
        record.session_id = session_id_ctx.get()  # set session_id value for formatting
        return True


def setup_common_logger(
        level: str = "INFO",
        log_dir: Optional[str] = None,
        max_bytes: int = 100 * 1024 * 1024,  # 100 MB
        backup_count: int = 20,
        is_sensitive_local: bool = True
) -> logging.Logger:
    """Setup logging."""
    level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    if root_logger.handlers:  # prevent double setup
        for handler in list(root_logger.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception as e:
                if not is_sensitive_local:
                    root_logger.info(f"Error closing handler: {e}")
                else:
                    root_logger.info(f"Error closing handler.")
        root_logger.handlers.clear()

    root_logger.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - "
        "session_id=%(session_id)s - %(message)s"
    )

    if log_dir is None:
        handler = logging.StreamHandler()
    else:
        log_dir_path = Path(log_dir)
        # 通用日志
        common_log_dir = log_dir_path / "common"
        common_log_path = common_log_dir / "common.log"
        handler = SafeRotatingFileHandler(
            filename=str(common_log_path),
            mode='a',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

        # warning日志，总是启用，但只记录用户设置级别及以上
        warning_log_path = common_log_dir / "common_warning.log"
        warning_handler = SafeRotatingFileHandler(
            filename=str(warning_log_path),
            mode='a',
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        warning_level = max(level, logging.WARNING)
        warning_handler.setLevel(warning_level)
        warning_handler.setFormatter(formatter)
        warning_handler.addFilter(SessionFilter())
        root_logger.addHandler(warning_handler)

    handler.setFormatter(formatter)
    handler.addFilter(SessionFilter())
    root_logger.addHandler(handler)

    return root_logger
