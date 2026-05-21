# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import apply_web_search_domain_constraints, run_web_search
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


class TestWebSearchDomainConstraints:
    """搜索引擎域名约束合并测试"""

    def test_apply_domain_constraints_merges_with_initialized_wrapper_config(self):
        mock_wrapper = Mock()
        mock_wrapper.include_domains = ["configured.com", "shared.com"]
        mock_wrapper.exclude_domains = ["blocked.com"]

        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = {"tavily": mock_wrapper}

            applied = apply_web_search_domain_constraints(
                "tavily",
                include_domains=["intent.com", "shared.com"],
                exclude_domains=["intent-blocked.com"],
            )

        assert applied is True
        assert mock_wrapper.include_domains == ["configured.com", "shared.com", "intent.com"]
        assert mock_wrapper.exclude_domains == ["blocked.com", "intent-blocked.com"]

    def test_apply_domain_constraints_ignores_unsupported_wrapper(self):
        mock_wrapper = Mock()
        mock_wrapper.include_domains = []
        mock_wrapper.exclude_domains = []

        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = {"google": mock_wrapper}

            applied = apply_web_search_domain_constraints("google", include_domains=["intent.com"])

        assert applied is False
        assert mock_wrapper.include_domains == []
        assert mock_wrapper.exclude_domains == []
