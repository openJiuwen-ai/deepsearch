# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import run_web_search
from openjiuwen_deepsearch.utils.rate_limiter_utils.qps_limiter import qps_rate_limiter


class TestWebSearchRateLimit:
    """run_web_search 限流集成测试"""

    @pytest.fixture
    def mock_web_search_context(self):
        """模拟 web_search_context"""
        mock_wrapper = AsyncMock()
        mock_wrapper.aresults = AsyncMock(return_value=[
            {"title": "Test Result", "url": "http://example.com", "content": "Test content"}
        ])
        return {"tavily": mock_wrapper}

    @pytest.mark.asyncio
    async def test_run_web_search_with_rate_limit(self, mock_web_search_context):
        """测试带限流的搜索功能"""
        qps_rate_limiter.set_max_qps(5)

        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = mock_web_search_context

            num_requests = 8
            start_time = time.time()
            tasks = [run_web_search(f"query {i}", "tavily") for i in range(num_requests)]
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start_time

            assert len(results) == num_requests
            expected_min_time = (num_requests - 5) / 5
            assert elapsed >= expected_min_time * 0.5

    @pytest.mark.asyncio
    async def test_run_web_search_no_limit(self, mock_web_search_context):
        """测试不限流场景"""
        qps_rate_limiter.set_max_qps(0)

        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = mock_web_search_context

            num_requests = 5
            start_time = time.time()
            tasks = [run_web_search(f"query {i}", "tavily") for i in range(num_requests)]
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start_time

            assert len(results) == num_requests
            assert elapsed < 1.0
