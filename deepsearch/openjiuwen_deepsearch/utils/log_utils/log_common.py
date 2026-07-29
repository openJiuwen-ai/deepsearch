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

DEFAULT_MAX_LOG_MESSAGE_LENGTH = 4096
DEFAULT_LOG_HEAD_LENGTH = 1600
DEFAULT_LOG_TAIL_LENGTH = 1600
PROJECT_LOGGER_WHITELIST = (
    "openjiuwen_deepsearch",
    "server",
    "__main__",
)


class SessionFilter(logging.Filter):
    """Injects session_id into every log record."""

    def filter(self, record):
        """session filter"""
        record.session_id = session_id_ctx.get()  # set session_id value for formatting
        return True


class ProjectLoggerFilter(logging.Filter):
    """Allow project loggers and third-party warning/error logs into common logs."""

    def __init__(self, allowed_logger_names: tuple[str, ...] = PROJECT_LOGGER_WHITELIST):
        super().__init__()
        self.allowed_logger_names = allowed_logger_names

    def filter(self, record):
        """Allow project logs and keep third-party warning/error visible."""
        logger_name = getattr(record, "name", "")
        for allowed_name in self.allowed_logger_names:
            if logger_name == allowed_name or logger_name.startswith(f"{allowed_name}."):
                return True
        return record.levelno >= logging.WARNING


class TruncatingFormatter(logging.Formatter):
    """Format log records and truncate long messages unless explicitly disabled."""

    def __init__(
            self,
            fmt: str,
            datefmt: str | None = None,
            max_message_length: int = DEFAULT_MAX_LOG_MESSAGE_LENGTH,
            head_length: int = DEFAULT_LOG_HEAD_LENGTH,
            tail_length: int = DEFAULT_LOG_TAIL_LENGTH,
    ):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.max_message_length = max_message_length
        self.head_length = head_length
        self.tail_length = tail_length

    def format(self, record):
        """Format a log record while truncating the main message when needed."""
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)

        message = record.getMessage()
        if not getattr(record, "skip_truncation", False):
            message = self._truncate_message(message)
        record.message = message

        formatted_message = self.formatMessage(record)
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            if formatted_message and formatted_message[-1] != "\n":
                formatted_message += "\n"
            formatted_message += record.exc_text
        if record.stack_info:
            if formatted_message and formatted_message[-1] != "\n":
                formatted_message += "\n"
            formatted_message += self.formatStack(record.stack_info)
        return formatted_message

    def _truncate_message(self, message: str) -> str:
        """Truncate long log messages while keeping both head and tail content."""
        if self.max_message_length <= 0 or len(message) <= self.max_message_length:
            return message

        omitted_len = max(len(message) - self.head_length - self.tail_length, 0)
        marker = (
            f"...(truncated, original_len={len(message)}, omitted_len={omitted_len})..."
        )

        available_budget = self.max_message_length - len(marker)
        if available_budget <= 0:
            return marker[:self.max_message_length]

        head_length = min(self.head_length, available_budget)
        tail_length = min(self.tail_length, max(available_budget - head_length, 0))

        if head_length + tail_length > available_budget:
            tail_length = max(available_budget - head_length, 0)

        truncated_message = (
            f"{message[:head_length]}"
            f"{marker}"
            f"{message[len(message) - tail_length:] if tail_length else ''}"
        )
        if len(truncated_message) <= self.max_message_length:
            return truncated_message
        return truncated_message[:self.max_message_length]


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

    formatter = TruncatingFormatter(
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
        warning_handler.addFilter(ProjectLoggerFilter())
        root_logger.addHandler(warning_handler)

    handler.setFormatter(formatter)
    handler.addFilter(SessionFilter())
    handler.addFilter(ProjectLoggerFilter())
    root_logger.addHandler(handler)

    return root_logger
