# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Logger that mimics print
"""

import logging

print_logger = logging.getLogger("print")
if not print_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    print_logger.addHandler(_handler)
    print_logger.setLevel(logging.WARNING)
print_hw = print_logger.warning
