# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import contextvars
import datetime
import logging
import logging.handlers
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler

# ContextVar for per-request session_id
session_id_ctx = contextvars.ContextVar("session_id", default="-")

# ContextVar for per-run isolation (per-call logger)
# 每个 run_jiuwen_workflow 调用设置唯一 run_id, asyncio task-local 自动隔离
run_id_ctx = contextvars.ContextVar("run_id", default="")

DEFAULT_MAX_LOG_MESSAGE_LENGTH = 4096
DEFAULT_LOG_HEAD_LENGTH = 1600
DEFAULT_LOG_TAIL_LENGTH = 1600
PROJECT_LOGGER_WHITELIST = (
    "openjiuwen_deepsearch",
    "server",
    "__main__",
)


@dataclass
class RotationConfig:
    """日志文件轮转配置,封装 max_bytes 和 backup_count。"""
    max_bytes: int = 100 * 1024 * 1024  # 100 MB
    backup_count: int = 20


@dataclass
class RunPrefix:
    """运行标识前缀,封装 date_str 和 run_prefix。"""
    date_str: str = ""
    run_prefix: str = ""


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


class RunIdFilter(logging.Filter):
    """只放行 run_id_ctx 匹配当前 handler 的日志记录。

    用于 per-run handler:每个 handler 持有一个 run_id,
    只捕获属于本次运行的日志,实现并发隔离。
    """

    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def filter(self, record):
        return run_id_ctx.get() == self.run_id


class ExcludeActiveRunFilter(logging.Filter):
    """排除 per-run 活跃期间的日志记录，使 init-time handler 不重复写入 per-run 日志。

    当 run_id_ctx 非空时（有活跃的 per-run），拒绝记录，由 per-run handler 负责捕获；
    当 run_id_ctx 为空时（无 per-run），允许记录，用于捕获服务级系统日志。

    注意: 依赖 run_id_ctx 的默认值为空值 (ContextVar(default=""))，
    使用 not 判断以兼容未来默认值调整为 None 的场景。
    """

    def filter(self, record):
        return not run_id_ctx.get()


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


def _generate_run_prefix(run_hash: Optional[str] = None) -> RunPrefix:
    """生成本次运行的唯一标识。

    Args:
        run_hash: 可选的文件名 hash。per-run handler 应传入 run_id[:8],
            使文件名 hash 与 run_id 关联,排障时可由 run_id 反查日志文件;
            None 时使用独立的 uuid4 hash (init 文件使用)。

    Returns:
        RunPrefix: date_str (YYYYMMDD) + run_prefix (YYYYMMDD_HHMMSS_hash)
    """
    # 使用本地时间,与日志内容 %(asctime)s (logging 默认 localtime) 保持一致
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    if run_hash is None:
        run_hash = uuid.uuid4().hex[:8]
    return RunPrefix(date_str=date_str, run_prefix=f"{date_str}_{time_str}_{run_hash}")


_COMMON_FORMATTER_FMT = (
    "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - "
    "session_id=%(session_id)s - %(message)s"
)


def _create_common_file_handlers(
        log_dir_path: Path,
        run_prefix: RunPrefix,
        rotation: RotationConfig,
        level: int = logging.INFO,
        run_id: Optional[str] = None,
) -> tuple[logging.Handler, logging.Handler]:
    """创建 common + warning 文件 handler (公共逻辑)。

    init (run_id=None) 和 per-run (run_id 非空) 共用此函数,
    per-run 时额外添加 RunIdFilter 实现隔离。

    Args:
        log_dir_path: 日志根目录
        run_prefix: 运行标识前缀 (date_str + run_prefix)
        rotation: 文件轮转配置
        level: 日志级别 (int), warning handler 取 max(level, WARNING)
        run_id: 非空时添加 RunIdFilter (per-run), None 时不添加 (init)

    Returns:
        (common_handler, warning_handler)
    """
    formatter = TruncatingFormatter(_COMMON_FORMATTER_FMT)

    # init-time 用 common_system_ 前缀, per-run 用 common_ 前缀, 便于区分系统级日志和报告级日志
    if run_id is None:
        common_prefix = "common_system"
        warning_prefix = "common_system_warning"
    else:
        common_prefix = "common"
        warning_prefix = "common_warning"

    common_log_dir = log_dir_path / "common" / run_prefix.date_str
    common_log_path = common_log_dir / f"{common_prefix}_{run_prefix.run_prefix}.log"
    common_handler = SafeRotatingFileHandler(
        filename=str(common_log_path),
        mode='a',
        maxBytes=rotation.max_bytes,
        backupCount=rotation.backup_count,
        encoding="utf-8",
        delay=True,
    )
    common_handler.setFormatter(formatter)
    if run_id is not None:
        common_handler.addFilter(RunIdFilter(run_id))
    else:
        # init-time handler: 排除 per-run 活跃期间的日志，避免与 per-run 文件重复
        common_handler.addFilter(ExcludeActiveRunFilter())
    common_handler.addFilter(SessionFilter())
    common_handler.addFilter(ProjectLoggerFilter())

    warning_log_path = common_log_dir / f"{warning_prefix}_{run_prefix.run_prefix}.log"
    warning_handler = SafeRotatingFileHandler(
        filename=str(warning_log_path),
        mode='a',
        maxBytes=rotation.max_bytes,
        backupCount=rotation.backup_count,
        encoding="utf-8",
        delay=True,
    )
    warning_handler.setLevel(max(level, logging.WARNING))
    warning_handler.setFormatter(formatter)
    if run_id is not None:
        warning_handler.addFilter(RunIdFilter(run_id))
    else:
        # init-time handler: 排除 per-run 活跃期间的日志，避免与 per-run 文件重复
        warning_handler.addFilter(ExcludeActiveRunFilter())
    warning_handler.addFilter(SessionFilter())
    warning_handler.addFilter(ProjectLoggerFilter())

    return common_handler, warning_handler


def setup_common_logger(
        level: str = "INFO",
        log_dir: Optional[str] = None,
        rotation: RotationConfig = RotationConfig(),
        is_sensitive_local: bool = True,
        run_prefix: RunPrefix = RunPrefix(),
) -> logging.Logger:
    """Setup logging.

    Args:
        level: 日志级别
        log_dir: 日志目录, None 输出到控制台
        rotation: 文件轮转配置
        is_sensitive_local: 是否有敏感信息
        run_prefix: 运行标识前缀 (date_str + run_prefix)
    """
    level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()

    # init 首次调用: 清空所有 handler
    if root_logger.handlers:
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

    if log_dir is None:
        handler = logging.StreamHandler()
        handler.setFormatter(TruncatingFormatter(_COMMON_FORMATTER_FMT))
        handler.addFilter(SessionFilter())
        handler.addFilter(ProjectLoggerFilter())
    else:
        log_dir_path = Path(log_dir)
        common_handler, warning_handler = _create_common_file_handlers(
            log_dir_path=log_dir_path,
            run_prefix=run_prefix,
            rotation=rotation,
            level=level,
        )
        root_logger.addHandler(warning_handler)
        handler = common_handler

    root_logger.addHandler(handler)

    return root_logger


def create_per_run_handler(
        log_dir: str,
        run_id: str,
        level: int = logging.INFO,
        rotation: RotationConfig = RotationConfig(),
        run_prefix: RunPrefix = RunPrefix(),
) -> list[logging.Handler]:
    """创建 per-run handler (common + warning),通过 RunIdFilter 隔离。

    返回 handler 列表,由调用方 (LogManager.new_run) 添加到 root logger。
    每个 handler 只捕获 run_id_ctx 匹配的日志记录。
    调用方需在结束时调用 LogManager.end_run(run_id) 清理。

    Args:
        log_dir: 日志根目录
        run_id: 本次运行的唯一标识
        level: 日志级别
        rotation: 文件轮转配置
        run_prefix: 运行标识前缀 (与 metrics 共享)

    Returns:
        handlers: 创建的 handler 列表 (common + warning)
    """
    log_dir_path = Path(log_dir)
    common_handler, warning_handler = _create_common_file_handlers(
        log_dir_path=log_dir_path,
        run_prefix=run_prefix,
        rotation=rotation,
        level=level,
        run_id=run_id,
    )
    return [common_handler, warning_handler]
