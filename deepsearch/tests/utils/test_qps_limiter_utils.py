# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time

import pytest

from openjiuwen_deepsearch.common.exception import CustomRuntimeException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.rate_limiter_utils.qps_limiter import QPSRateLimiter


class TestQPSRateLimiter:
    """QPSRateLimiter 单元测试"""

    @pytest.mark.asyncio
    async def test_acquire_no_limit(self):
        """测试 QPS<=0 时不限流"""
        limiter = QPSRateLimiter()
        for qps in [None, 0, -1]:
            limiter.set_max_qps(qps)
            start_time = time.time()
            await limiter.acquire()
            elapsed = time.time() - start_time
            assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_acquire_with_limit(self):
        """测试正常 QPS 限流"""
        limiter = QPSRateLimiter()
        max_qps = 5
        limiter.set_max_qps(max_qps)
        num_requests = 8

        start_time = time.time()
        tasks = [limiter.acquire() for _ in range(num_requests)]
        await asyncio.gather(*tasks)
        elapsed = time.time() - start_time

        expected_min_time = (num_requests - max_qps) / max_qps
        assert elapsed >= expected_min_time * 0.5

    @pytest.mark.asyncio
    async def test_calculate_timeout(self):
        """测试超时时间计算"""
        limiter = QPSRateLimiter()

        limiter.set_max_qps(10)
        timeout = limiter._calculate_timeout()
        assert timeout == 3.0

        limiter.set_max_qps(1)
        timeout = limiter._calculate_timeout()
        assert timeout == 3.0

        limiter.set_max_qps(0.5)
        timeout = limiter._calculate_timeout()
        assert timeout == 6.0

        limiter.set_max_qps(0.1)
        timeout = limiter._calculate_timeout()
        assert timeout == 30.0

        limiter.set_max_qps(0.01)
        timeout = limiter._calculate_timeout()
        assert timeout == 60.0

    @pytest.mark.asyncio
    async def test_acquire_timeout_with_retry(self):
        """测试超时后重试机制"""
        limiter = QPSRateLimiter()
        limiter.set_max_qps(10)

        call_count = 0

        async def mock_acquire_always_timeout(timeout):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()

        original_acquire = limiter._acquire_with_timeout
        limiter._acquire_with_timeout = mock_acquire_always_timeout

        with pytest.raises(CustomRuntimeException) as exc_info:
            await limiter.acquire()

        assert exc_info.value.error_code == StatusCode.RATE_LIMIT_TIMEOUT_ERROR.code
        assert call_count == 2

        limiter._acquire_with_timeout = original_acquire

    @pytest.mark.asyncio
    async def test_acquire_success_after_retry(self):
        """测试重试后成功获取许可"""
        limiter = QPSRateLimiter()
        limiter.set_max_qps(10)

        call_count = 0
        original_acquire = limiter._acquire_with_timeout

        async def mock_acquire_once_fail(timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return await original_acquire(timeout)

        limiter._acquire_with_timeout = mock_acquire_once_fail

        await limiter.acquire()
        assert call_count == 2

        limiter._acquire_with_timeout = original_acquire
