# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""最小 LogManager：stdlib logging + 敏感脱敏开关。

`init()` 供应用入口调用一次；库代码只用 `get_logger()`，不配置日志。
默认不会改动宿主应用已有的 root handler：若 root 上已有 handler，
`init()` 只记录脱敏开关与日志目录而不重配（需要接管时显式传 `force=True`）。
当前版本不含日志轮转与路径安全校验，长驻服务建议自行配置 handler。"""

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
        log_file_name: str = "app.log",
        force: bool = False,
    ) -> None:
        """配置根日志。

        force=False（默认）时不接管宿主应用已有的日志配置：root 上已有 handler
        则跳过重配，仅保留脱敏开关与日志目录设置。作为独立进程运行（CLI、
        服务入口）且需要统一日志格式时，显式传 force=True。
        """
        cls._sensitive = is_sensitive
        cls._log_dir = log_dir
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        root_configured = bool(logging.getLogger().handlers)
        if root_configured and not force:
            cls._initialized = True
            return

        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if log_dir:
            handlers.append(logging.FileHandler(os.path.join(log_dir, log_file_name)))
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
