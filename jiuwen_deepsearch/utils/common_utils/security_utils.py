# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os

from pathlib import Path
from typing import Optional

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode

_SAFE_BASE = os.path.realpath("./output")


def zero_secret(ba: bytearray):
    '''
    将存储在可变 bytearray 中的敏感数据（如密钥）清零。
    '''
    for i, _ in enumerate(ba):
        ba[i] = 0


def ensure_safe_directory(file_dir: Optional[str], safe_base: Optional[str] = None) -> Optional[str]:
    """
    安全验证文件目录路径，并设置安全权限（0o750）。
    Args:
        file_dir: 待验证的文件目录路径（若为 None 则直接返回 None）
        safe_base: 安全基目录路径（默认使用 _SAFE_BASE）
    Returns:
        规范化后的绝对路径字符串，或 None（当 file_dir 为 None 时）
    """
    if file_dir is None:
        return None

    # 使用传入的 safe_base 或 默认值
    base = safe_base or _SAFE_BASE

    try:
        target = Path(file_dir).resolve()
        safe_base_path = Path(base).resolve()
    except Exception as e:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_FILE_DIR_INVALID.code,
            message=StatusCode.PARAM_CHECK_ERROR_FILE_DIR_INVALID.errmsg.format(
                file_dir=file_dir,
            ),
        ) from e

    # 安全校验：确保 target 是 safe_base 的子路径
    try:
        target.relative_to(safe_base_path)
    except ValueError as e:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_FILE_DIR_UNSAFE.code,
            message=StatusCode.PARAM_CHECK_ERROR_FILE_DIR_UNSAFE.errmsg.format(
                file_dir=file_dir,
                safe_base=str(safe_base_path),
            ),
        ) from e

    # 创建目录并强制设置权限
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chmod(str(target), 0o750)  # 防 umask 干扰

    return str(target)
