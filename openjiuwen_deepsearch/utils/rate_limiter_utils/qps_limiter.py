# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import functools
import logging
from typing import Optional, Union

from aiolimiter import AsyncLimiter

from openjiuwen_deepsearch.common.exception import CustomRuntimeException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config

logger = logging.getLogger(__name__)


class QPSRateLimiter:
    """
    QPS 限流器（基于 aiolimiter 实现）

    使用令牌桶算法实现异步 QPS 限流，支持动态配置 QPS 值。
    当 QPS 设置为 0 或负数时，不启用限流功能。
    支持浮点数 QPS，如 0.5 表示每 2 秒 1 个请求。
    """

    def __init__(self):
        """初始化限流器，从配置中读取 QPS 限制"""
        self._limiter: Optional[AsyncLimiter] = None
        self._max_qps: Optional[float] = self._load_max_qps_from_config()

    @staticmethod
    def _load_max_qps_from_config() -> Optional[float]:
        """从配置中加载 QPS 限制"""
        try:
            config = Config()
            if hasattr(config, 'service_config') and hasattr(config.service_config, 'web_search_max_qps'):
                return config.service_config.web_search_max_qps
        except Exception as e:
            logger.warning(f"[QPSRateLimiter] Failed to load max_qps from config: {e}")
        return None

    def get_max_qps(self) -> Optional[float]:
        """获取当前 QPS 限制值"""
        return self._max_qps

    def set_max_qps(self, max_qps: Optional[Union[int, float]]) -> None:
        """设置 QPS 限制值"""
        self._max_qps = float(max_qps) if max_qps is not None else None

    def _get_limiter(self) -> Optional[AsyncLimiter]:
        """获取或创建限流器实例，当 max_qps 变化时重建"""
        max_qps = self._max_qps
        if max_qps is None or max_qps <= 0:
            return None

        if self._limiter is None:
            if max_qps >= 1:
                self._limiter = AsyncLimiter(max_rate=max_qps, time_period=1.0)
            else:
                self._limiter = AsyncLimiter(max_rate=1, time_period=1.0 / max_qps)
            logger.info(f"[QPSRateLimiter] Created new limiter with max_qps={max_qps}")

        return self._limiter

    def _calculate_timeout(self) -> float:
        """
        计算超时时间，与 QPS 挂钩

        超时时间计算策略：
        - QPS >= 1: timeout = 3.0 (最少 3 秒等待)
        - QPS < 1: timeout = 3.0 / max_qps (低 QPS 需要更长等待时间)
        - 最小超时时间: 3 秒
        - 最大超时时间: 60 秒

        Returns:
            float: 超时时间（秒）
        """
        max_qps = self._max_qps
        if max_qps is None or max_qps <= 0:
            return 3.0

        timeout = 3.0 / max_qps
        timeout = max(3.0, min(timeout, 60.0))
        return timeout

    async def _acquire_with_timeout(self, timeout: float) -> bool:
        """
        带超时的获取限流许可

        Args:
            timeout: 超时时间（秒）

        Returns:
            bool: 是否成功获取许可

        Raises:
            asyncio.TimeoutError: 获取许可超时
        """
        limiter = self._get_limiter()
        if limiter is None:
            return True

        if not limiter.has_capacity():
            logger.info(f"[QPSRateLimiter] Rate limit exceeded, waiting for permit, max_qps={self._max_qps}")

        await asyncio.wait_for(limiter.acquire(1), timeout=timeout)
        logger.info(f"[QPSRateLimiter] Request permitted, max_qps={self._max_qps}")
        return True

    async def acquire(self) -> None:
        """
        获取限流许可，支持超时和重试机制

        超时后会自动重试一次，如果仍然超时则抛出异常。

        Raises:
            CustomRuntimeException: 限流超时异常
        """
        limiter = self._get_limiter()
        if limiter is None:
            return

        timeout = self._calculate_timeout()
        max_attempts = 2

        for attempt in range(max_attempts):
            try:
                await self._acquire_with_timeout(timeout)
                return
            except asyncio.TimeoutError as e:
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"[QPSRateLimiter] Rate limit timeout, retrying... "
                        f"(attempt {attempt + 1}/{max_attempts}), max_qps={self._max_qps}, timeout={timeout:.1f}s"
                    )
                else:
                    logger.error(
                        f"[QPSRateLimiter] Rate limit timeout after {max_attempts} attempts, "
                        f"max_qps={self._max_qps}, timeout={timeout:.1f}s"
                    )
                    raise CustomRuntimeException(
                        StatusCode.RATE_LIMIT_TIMEOUT_ERROR.code,
                        StatusCode.RATE_LIMIT_TIMEOUT_ERROR.errmsg.format(
                            timeout=timeout, max_qps=self._max_qps
                        )
                    ) from e


qps_rate_limiter = QPSRateLimiter()


def qps_rate_limit_async(func):
    """
    异步 QPS 限流装饰器

    用于装饰异步函数，在函数执行前自动获取限流许可。
    限流参数从配置中读取 web_search_max_qps。
    支持超时机制和重试逻辑。

    用法:
        @qps_rate_limit_async
        async def my_async_function():
            ...
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        await qps_rate_limiter.acquire()
        return await func(*args, **kwargs)
    return wrapper
