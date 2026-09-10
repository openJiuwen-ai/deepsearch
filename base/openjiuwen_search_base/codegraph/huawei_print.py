# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Logger that mimics print
"""

import logging

logging.basicConfig(format="%(message)s", level=logging.WARNING)

print_logger = logging.getLogger("print")
print_hw = print_logger.warning
