# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import logging
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)


class SafeRotatingFileHandler(RotatingFileHandler):
    """安全的日志轮转处理器"""
    ACTIVE_FILE_MODE = 0o640  # 活跃日志文件
    ROTATED_FILE_MODE = 0o440  # 归档日志文件
    DIRECTORY_MODE = 0o750  # 日志目录

    def __init__(self, filename, **kwargs):
        # 确保目录存在
        filename = str(filename)
        log_dir = os.path.dirname(filename)
        if log_dir:
            os.makedirs(log_dir, mode=self.DIRECTORY_MODE, exist_ok=True)
            # 同步权限
            try:
                os.chmod(log_dir, self.DIRECTORY_MODE)
            except PermissionError:
                logger.warning("Unable to set log directory permissions: %s", log_dir)
                pass

        kwargs.setdefault('delay', True)
        super().__init__(filename, **kwargs)

    def doRollover(self):
        """执行轮转并设置所有相关文件权限"""
        super().doRollover()

        # 设置所有归档文件的权限
        if self.backupCount > 0:
            for i in range(1, self.backupCount + 1):
                rotated_file = f"{self.baseFilename}.{i}"
                if os.path.exists(rotated_file):
                    self._chmod(rotated_file, self.ROTATED_FILE_MODE)

        # 新的当前文件权限正确
        if os.path.exists(self.baseFilename):
            self._chmod(self.baseFilename, self.ACTIVE_FILE_MODE)

    def _chmod(self, path, mode):
        """权限设置，文件不存在时静默跳过"""
        try:
            os.chmod(path, mode)
        except (FileNotFoundError, PermissionError):
            logger.warning("Unable to set log file permissions: %s", path)
            pass

    def _open(self):
        """打开文件并设置权限"""
        stream = super()._open()
        self._chmod(self.baseFilename, self.ACTIVE_FILE_MODE)
        return stream
