# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LogManager —— 由 openjiuwen-search-base 提供（2026-07-29 提取）。

本壳保留 codesearch 的历史默认日志文件名（codesearch.log），
避免提取后接线时的静默漂移；直接使用 `init()` 即得产品默认。
"""

from typing import Optional

from openjiuwen_search_base.logging_utils import LogManager, get_logger, redact

__all__ = ["DEFAULT_LOG_FILE_NAME", "LogManager", "get_logger", "init", "redact"]

DEFAULT_LOG_FILE_NAME = "codesearch.log"


def init(
    log_dir: Optional[str] = None,
    level: str = "INFO",
    is_sensitive: bool = False,
    log_file_name: str = DEFAULT_LOG_FILE_NAME,
    force: bool = False,
) -> None:
    """codesearch 默认参数的 LogManager.init 封装。

    force 的含义见 `LogManager.init`：默认不接管宿主应用已有的日志配置。
    """
    LogManager.init(
        log_dir=log_dir,
        level=level,
        is_sensitive=is_sensitive,
        log_file_name=log_file_name,
        force=force,
    )
