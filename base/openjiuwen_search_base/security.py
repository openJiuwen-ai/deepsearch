# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""密钥处理工具。

约定（与 openJiuwen 系列产品一致）：密钥在配置模型中以 `bytearray` 存储，
调用外部服务时才 `reveal_secret` 解码为字符串，用完可 `zero_secret` 就地清零，
避免不可变 `str` 在进程内长期驻留且无法擦除。
"""

from typing import Union

SecretInput = Union[str, bytes, bytearray, None]


def to_secret(value: SecretInput) -> bytearray:
    """把任意输入规范化为 bytearray 密钥（None/空 → 空 bytearray）。"""
    if value is None:
        return bytearray()
    if isinstance(value, bytearray):
        return value
    if isinstance(value, bytes):
        return bytearray(value)
    return bytearray(value, encoding="utf-8")


def reveal_secret(secret: SecretInput) -> str:
    """解码为字符串，仅在真正调用外部服务时使用，不要长期持有返回值。"""
    if secret is None:
        return ""
    if isinstance(secret, str):
        return secret
    return bytes(secret).decode("utf-8")


def zero_secret(secret: SecretInput) -> None:
    """将 bytearray 中的敏感数据就地清零（非 bytearray 输入为空操作）。"""
    if isinstance(secret, bytearray):
        for i, _ in enumerate(secret):
            secret[i] = 0
