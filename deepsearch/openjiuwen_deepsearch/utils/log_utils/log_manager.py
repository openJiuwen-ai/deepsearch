# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import datetime
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NODE_DEBUG_LOGGER
from openjiuwen_deepsearch.utils.debug_utils.node_debug import setup_debug_logger
from openjiuwen_deepsearch.utils.log_utils.log_common import (
    setup_common_logger,
    create_per_run_handler,
    RotationConfig,
    RunPrefix,
    _generate_run_prefix,
)
from openjiuwen_deepsearch.utils.log_utils.log_metrics import (
    setup_metrics_logger,
    create_per_run_metrics_handler,
)
from openjiuwen_deepsearch.utils.log_utils.log_interface import setup_interface_logger


class LogManager:
    _initialized = False
    _is_sensitive = False
    _level: str = "INFO"
    _max_bytes: int = 100 * 1024 * 1024
    _backup_count: int = 20
    _current_log_dir: Optional[str] = None
    _active_run_handlers: dict[str, list[logging.Handler]] = {}
    _log_retention_days: int = 30
    _SAFE_BASE = os.path.realpath("./output/logs")
    _THIRD_PARTY_LOGGERS = (
        "openai",
        "openai._base_client",
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.proxy",
        "asyncio",
    )

    @classmethod
    def init(
            cls,
            log_dir: Optional[str] = None,
            level: str = "INFO",
            rotation: RotationConfig = RotationConfig(),
            is_sensitive: bool = True,
            log_retention_days: int = 30,
    ):
        """
        Args:
            log_dir: 日志目录，None输出到控制台
            level: 日志级别
            rotation: 文件轮转配置 (max_bytes + backup_count)
            is_sensitive: 是否有敏感信息，若为True则对日志脱敏处理
            log_retention_days: 日志保留天数,0表示不清理
        """
        if cls._initialized:
            return

        max_bytes = rotation.max_bytes
        backup_count = rotation.backup_count
        cls._validate_init_args(level, max_bytes, backup_count, is_sensitive)
        log_dir = cls._safe_log_dir(log_dir)

        # 设置通用日志 + 打点计时日志 (共享同一 date_str/run_prefix)
        init_hash = uuid.uuid4().hex[:8]
        run_prefix = _generate_run_prefix(init_hash)
        setup_common_logger(
            level=level,
            log_dir=log_dir,
            rotation=rotation,
            is_sensitive_local=is_sensitive,
            run_prefix=run_prefix,
        )
        # 打点计时日志
        setup_metrics_logger(
            log_dir=log_dir,
            level=getattr(logging, level.upper(), logging.INFO),
            rotation=rotation,
            is_sensitive=is_sensitive,
            run_prefix=run_prefix,
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

        cls._configure_known_third_party_loggers()
        cls._is_sensitive = is_sensitive
        cls._level = level
        cls._max_bytes = max_bytes
        cls._backup_count = backup_count
        cls._current_log_dir = log_dir
        cls._log_retention_days = log_retention_days
        cls._initialized = True

        # 清理过期日志
        cls._cleanup_old_logs()

    @classmethod
    def new_run(cls) -> str:
        """为新的运行创建 per-run handler,添加到 root logger。

        使用 RunIdFilter 按 run_id 隔离日志,不替换 init() 创建的 handler,
        因此并发安全。调用方需在运行结束后调用 end_run() 清理。

        Returns:
            run_id 字符串,用于 set_run_context 和 end_run。未初始化或
            StreamHandler 模式时返回空字符串。
        """
        if not cls._initialized:
            return ""

        # 清理过期日志
        cls._cleanup_old_logs()

        log_dir = cls._current_log_dir
        if log_dir is None:
            return ""  # StreamHandler 模式,无需创建 per-run 文件

        run_id = uuid.uuid4().hex
        level_val = getattr(logging, cls._level.upper(), logging.INFO)
        rotation = RotationConfig(max_bytes=cls._max_bytes, backup_count=cls._backup_count)
        # 共享同一 run_prefix, 确保 common 与 metrics 文件前缀一致
        run_prefix = _generate_run_prefix(run_id[:8])
        handlers = create_per_run_handler(
            log_dir=log_dir,
            run_id=run_id,
            level=level_val,
            rotation=rotation,
            run_prefix=run_prefix,
        )
        metrics_handlers = create_per_run_metrics_handler(
            log_dir=log_dir,
            run_id=run_id,
            level=level_val,
            rotation=rotation,
            run_prefix=run_prefix,
        )

        root_logger = logging.getLogger()
        for handler in handlers:
            root_logger.addHandler(handler)

        # metrics logger propagate=False,per-run handler 需挂到 metrics logger
        metrics_logger = logging.getLogger("metrics")
        for handler in metrics_handlers:
            metrics_logger.addHandler(handler)

        cls._active_run_handlers[run_id] = handlers + metrics_handlers
        # 记录 run_id 与文件名前缀的映射,便于排障时由 run_id 反查日志文件
        # (此时 run_id_ctx 尚未设置,仅落入 init 全量文件,不会污染 per-run 文件)
        # 使用项目 logger (openjiuwen_deepsearch.*) 以通过 ProjectLoggerFilter
        logging.getLogger("openjiuwen_deepsearch.log_manager").info(
            "per-run logging started: run_id=%s, log_prefix=%s", run_id, run_prefix.run_prefix
        )
        return run_id

    @classmethod
    def end_run(cls, run_id: str):
        """移除并关闭 per-run handler。"""
        if not run_id:
            return
        handlers = cls._active_run_handlers.pop(run_id, None)
        if not handlers:
            return
        root_logger = logging.getLogger()
        metrics_logger = logging.getLogger("metrics")
        for handler in handlers:
            try:
                handler.flush()
                handler.close()
            except Exception as e:
                # 与 setup_common_logger 一致: 敏感模式下不记录异常详情
                detail = "" if cls._is_sensitive else f": {e}"
                root_logger.warning(f"Error closing per-run handler{detail}")
            root_logger.removeHandler(handler)
            metrics_logger.removeHandler(handler)

    @classmethod
    def _cleanup_old_logs(cls):
        """清理超过保留天数的 common 和 metrics 日志目录。

        扫描 common/ 和 metrics/ 下的 YYYYMMDD 日期文件夹,
        删除早于 cutoff 日期的文件夹。log_retention_days=0 时不清理。
        """
        if cls._log_retention_days <= 0:
            return

        log_dir = cls._current_log_dir
        if log_dir is None:
            return

        cutoff = datetime.date.today() - datetime.timedelta(days=cls._log_retention_days)
        cutoff_str = cutoff.strftime("%Y%m%d")
        log_manager_logger = logging.getLogger("openjiuwen_deepsearch.log_manager")

        log_dir_path = Path(log_dir)
        for subdir_name in ("common", "metrics"):
            subdir = log_dir_path / subdir_name
            if not subdir.exists():
                continue
            for date_dir in subdir.iterdir():
                if not date_dir.is_dir():
                    continue
                name = date_dir.name
                # 文件夹名应为 YYYYMMDD 且早于 cutoff
                if len(name) == 8 and name.isdigit() and name < cutoff_str:
                    try:
                        shutil.rmtree(date_dir)
                        log_manager_logger.info(
                            "Removed old log directory: %s/%s", subdir_name, name
                        )
                    except Exception as e:
                        detail = "" if cls._is_sensitive else f": {e}"
                        log_manager_logger.warning(
                            "Failed to remove old log directory %s/%s%s",
                            subdir_name, name, detail,
                        )

    @classmethod
    def is_sensitive(cls) -> bool:
        """
        获取敏感信息设置
        """
        return cls._is_sensitive

    @classmethod
    def get_log_dir(cls) -> Optional[str]:
        """
        获取当前日志目录
        Returns:
            当前日志目录路径，如果未初始化则返回None
        """
        return cls._current_log_dir

    @classmethod
    def _configure_known_third_party_loggers(cls):
        """Suppress third-party debug/info logs while preserving warning/error logs."""
        for logger_name in cls._THIRD_PARTY_LOGGERS:
            third_party_logger = logging.getLogger(logger_name)
            third_party_logger.disabled = False
            third_party_logger.setLevel(logging.WARNING)
            third_party_logger.propagate = True

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
