# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.common.status_code import StatusCode


class CodeSearchException(Exception):
    """项目基础异常，携带错误码。"""

    def __init__(self, status: StatusCode, **fmt: object) -> None:
        self.status = status
        self.code = status.code
        try:
            self.message = status.errmsg.format(**fmt)
        except (KeyError, IndexError):
            self.message = status.errmsg
        super().__init__(f"[{self.code}] {self.message}")


class CustomValueException(CodeSearchException):
    """参数/取值类错误（命名对齐 deepsearch 惯例）。"""
