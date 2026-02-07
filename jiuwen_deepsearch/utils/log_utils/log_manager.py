# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import os
from pathlib import Path
from typing import Optional

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.utils.constants_utils.node_constants import NODE_DEBUG_LOGGER
from jiuwen_deepsearch.utils.debug_utils.node_debug import setup_debug_logger
from jiuwen_deepsearch.utils.log_utils.log_common import setup_common_logger
from jiuwen_deepsearch.utils.log_utils.log_metrics import setup_metrics_logger
from jiuwen_deepsearch.utils.log_utils.log_interface import setup_interface_logger


class LogManager:
    _initialized = False
    _is_sensitive = True
    _SAFE_BASE = os.path.realpath("./logs")

    @classmethod
    def init(
            cls,
            log_dir: Optional[str] = None,
            level: str = "INFO",
            max_bytes: int = 100 * 1024 * 1024,  # 100 MB
            backup_count: int = 20,
            is_sensitive: bool = True,
    ):
        """
        Args:
            log_dir: 日志目录，None输出到控制台
            level: 日志级别
            max_bytes: 单个日志文件大小限制 (Min: 0, Max: 1000MB)
            backup_count: 文件数量 (Min: 0, Max: 1000)
            is_sensitive: 是否有敏感信息，若为True则对日志脱敏处理
        """
        if cls._initialized:
            return

        cls._validate_init_args(level, max_bytes, backup_count, is_sensitive)
        log_dir = cls._safe_log_dir(log_dir)

        # 设置通用日志
        setup_common_logger(level, log_dir, max_bytes, backup_count, is_sensitive)
        # 打点计时日志
        setup_metrics_logger(
            log_dir=log_dir,
            level=getattr(logging, level.upper(), logging.INFO),
            max_bytes=max_bytes,
            backup_count=backup_count,
            is_sensitive=is_sensitive
        )
        # 接口日志
        setup_interface_logger(
            log_dir=log_dir,
            level=getattr(logging, level.upper(), logging.INFO),
            max_bytes=max_bytes,
            backup_count=backup_count
        )
        # 节点格式化debug日志
        node_debug_enable = Config().service_config.model_dump().get("node_debug_enable", False)
        if node_debug_enable:
            setup_debug_logger(
                name=NODE_DEBUG_LOGGER,
                log_dir=log_dir,
                max_bytes=max_bytes,
                backup_count=backup_count,
                is_sensitive=is_sensitive
            )

        cls._is_sensitive = is_sensitive
        cls._initialized = True

    @classmethod
    def is_sensitive(cls) -> bool:
        """
        获取敏感信息设置
        """
        return cls._is_sensitive

    @classmethod
    def _validate_init_args(
            cls,
            level: str,
            max_bytes: int,
            backup_count: int,
            is_sensitive: bool,
    ):
        # 校验 is_sensitive 类型
        if not isinstance(is_sensitive, bool):
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_BOOL.code,
                message=StatusCode.PARAM_CHECK_ERROR_FIELD_NOT_BOOL.errmsg.format(
                    field='is_sensitive'
                )
            )

        # 校验 level
        if not isinstance(level, str):
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH.code,
                message=StatusCode.PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH.errmsg.format(
                    expected_type='str', field='level'
                )
            )
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level.upper() not in valid_levels:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_PARAM_NOT_IN_RANGE.code,
                message=StatusCode.PARAM_CHECK_ERROR_PARAM_NOT_IN_RANGE.errmsg.format(
                    param='level',
                    param_range=str(valid_levels)
                )
            )

        # 校验 max_bytes (Min: 0, Max: 1000MB)
        limit_max_bytes = 1000 * 1024 * 1024
        limit_min_bytes = 0

        if not isinstance(max_bytes, int):
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH.code,
                message=StatusCode.PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH.errmsg.format(
                    expected_type='int', field='max_bytes'
                )
            )
        if not limit_min_bytes <= max_bytes <= limit_max_bytes:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_VAL_OUT_OF_RANGE.code,
                message=StatusCode.PARAM_CHECK_ERROR_VAL_OUT_OF_RANGE.errmsg.format(
                    param='max_bytes', value=max_bytes, min_val=limit_min_bytes, max_val=limit_max_bytes
                )
            )

        # 校验 backup_count (Min: 0, Max: 1000)
        limit_max_backup = 1000
        limit_min_backup = 0

        if not isinstance(backup_count, int):
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH.code,
                message=StatusCode.PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH.errmsg.format(
                    expected_type='int', field='backup_count'
                )
            )
        if not limit_min_backup <= backup_count <= limit_max_backup:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_VAL_OUT_OF_RANGE.code,
                message=StatusCode.PARAM_CHECK_ERROR_VAL_OUT_OF_RANGE.errmsg.format(
                    param='backup_count', value=backup_count, min_val=limit_min_backup, max_val=limit_max_backup
                )
            )

    @classmethod
    def _safe_log_dir(cls, log_dir: Optional[str]) -> Optional[str]:
        """
        安全日志路径验证，并控制日志目录权限
        Args:
            log_dir: 日志目录路径（None表示输出到控制台）
        Returns:
            规范化后的路径字符串
        """
        if log_dir is None:
            return None

        try:
            target = Path(log_dir).resolve()
        except Exception as e:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_LOG_DIR_INVALID.code,
                message=StatusCode.PARAM_CHECK_ERROR_LOG_DIR_INVALID.errmsg.format(
                    log_dir=log_dir,
                ),
            ) from e
        safe_base = Path(cls._SAFE_BASE).resolve()

        try:
            target.relative_to(safe_base)  # 验证是否为子路径
        except ValueError as e:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_LOG_DIR_UNSAFE.code,
                message=StatusCode.PARAM_CHECK_ERROR_LOG_DIR_UNSAFE.errmsg.format(
                    log_dir=log_dir,
                    safe_base=str(safe_base),
                ),
            ) from e

        target.mkdir(mode=0o750, parents=True, exist_ok=True)
        # 显式设置权限，防止umask影响
        os.chmod(str(target), 0o750)

        return str(target)
