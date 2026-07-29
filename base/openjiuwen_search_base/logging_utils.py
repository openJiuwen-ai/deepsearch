# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""最小 LogManager：stdlib logging + 敏感脱敏开关。

⚠️ 库副作用提示：`init()` 使用 `logging.basicConfig(force=True)`，会**覆盖宿主
应用已有的 root handler**——仅供应用入口调用一次，库代码只用 `get_logger()`。
路径安全校验（safe base 校验、文件/目录权限位）与轮转将随 deepsearch
完整版 log_utils 的收编一并补齐（见 base/README 待办）。"""

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
    ) -> None:
        cls._sensitive = is_sensitive
        cls._log_dir = log_dir
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
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
