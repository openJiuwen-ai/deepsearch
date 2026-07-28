# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""最小 LogManager：stdlib logging + 敏感脱敏开关。

对齐 deepsearch LogManager 的使用面（init / get_logger / is_sensitive），
实现保持精简；需要文件轮转/指标时再对齐其完整版。
"""

import logging
import os
from typing import Optional

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class LogManager:
    _initialized: bool = False
    _sensitive: bool = False
    _log_dir: Optional[str] = None

    @classmethod
    def init(
        cls,
        log_dir: Optional[str] = None,
        level: str = "INFO",
        is_sensitive: bool = False,
    ) -> None:
        cls._sensitive = is_sensitive
        cls._log_dir = log_dir
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            handlers.append(logging.FileHandler(os.path.join(log_dir, "codesearch.log")))
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                            format=_DEFAULT_FORMAT, handlers=handlers, force=True)
        cls._initialized = True

    @classmethod
    def is_sensitive(cls) -> bool:
        return cls._sensitive

    @classmethod
    def get_log_dir(cls) -> Optional[str]:
        return cls._log_dir


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact(value: object) -> str:
    """敏感模式下用于日志的脱敏包装。"""
    return "***" if LogManager.is_sensitive() else str(value)
