#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

"""
openJiuwen-DeepSearch Server - 主入口

这是应用的主入口文件，用于启动 openJiuwen-DeepSearch 后端服务。
实际实现位于 server/main.py
"""

import logging
from server.main import main
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

LogManager.init(
    log_dir="./output/logs",
    max_bytes=100 * 1024 * 1024,
    backup_count=20,
    level="INFO",
    is_sensitive=False
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    main()
