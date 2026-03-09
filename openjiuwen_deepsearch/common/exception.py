# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

MAGIC_CODE = "\t"


class CustomException(Exception):
    def __init__(self, error_code: int, message: str) -> None:
        super().__init__()
        self._error_code = error_code
        self._message = message

    def __str__(self):
        return f"[{self._error_code}] {self._message}{MAGIC_CODE}"

    @property
    def error_code(self) -> int:
        """Return error code"""
        return self._error_code

    @property
    def message(self) -> str:
        """Return excepiton message."""
        return self._message


class CustomValueException(CustomException, ValueError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomNotImplementedException(CustomException, NotImplementedError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomRuntimeException(CustomException, RuntimeError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomFileExistsException(CustomException, FileExistsError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomFileNotFoundException(CustomException, FileNotFoundError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomIndexException(CustomException, IndexError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomKeyException(CustomException, KeyError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomTypeException(CustomException, TypeError):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)


class CustomJiuWenBaseException(CustomException, Exception):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)
