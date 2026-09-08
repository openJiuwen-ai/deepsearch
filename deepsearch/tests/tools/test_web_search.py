# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
import httpx
from pydantic import SecretStr

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import TavilySearchAPIWrapper
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed import (
    PubMedSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import (
    apply_web_search_domain_constraints,
    apply_web_search_temporal_scope,
    create_web_search_tool,
    run_web_search,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import web_search_context
from openjiuwen_deepsearch.utils.rate_limiter_utils.qps_limiter import qps_rate_limiter


def test_web_search_tool_card_does_not_expose_temporal_parameters():
    """统一搜索工具签名必须保持 query 与引擎名两个字段。"""
    properties = create_web_search_tool().card.input_params["properties"]

    assert set(properties) == {"query", "search_engine_name"}


@pytest.mark.asyncio
async def test_unknown_pubmed_failure_is_not_retryable():
    wrapper = PubMedSearchAPIWrapper()
    wrapper.aresults = AsyncMock(side_effect=RuntimeError("429 Too Many Requests"))

    with patch(
        'openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context'
    ) as mock_ctx, patch.object(qps_rate_limiter, "acquire", new=AsyncMock()):
        mock_ctx.get.return_value = {"pubmed": wrapper}
        result = await run_web_search("medical LLM calibration", "pubmed")

    assert result["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["pubmed", "arxiv", "semantic_scholar"])
@pytest.mark.parametrize("status_code", [400, 429, 503])
async def test_scholarly_search_http_failures_are_not_retryable(engine, status_code):
    wrapper = PubMedSearchAPIWrapper()
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(status_code, request=request)
    wrapper.aresults = AsyncMock(side_effect=httpx.HTTPStatusError(
        f"status {status_code}", request=request, response=response,
    ))

    with patch(
        'openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context'
    ) as mock_ctx, patch.object(qps_rate_limiter, "acquire", new=AsyncMock()):
        mock_ctx.get.return_value = {engine: wrapper}
        result = await run_web_search("medical LLM calibration", engine)

    assert result["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["pubmed", "arxiv", "semantic_scholar"])
async def test_scholarly_search_connection_failure_is_not_retryable(engine):
    wrapper = PubMedSearchAPIWrapper()
    request = httpx.Request("GET", "https://example.com")
    wrapper.aresults = AsyncMock(side_effect=httpx.ConnectError("connection failed", request=request))

    with patch(
        'openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context'
    ) as mock_ctx, patch.object(qps_rate_limiter, "acquire", new=AsyncMock()):
        mock_ctx.get.return_value = {engine: wrapper}
        result = await run_web_search("medical LLM calibration", engine)

    assert result["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 503])
async def test_regular_web_search_transient_http_failures_remain_retryable(status_code):
    wrapper = TavilySearchAPIWrapper()
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(status_code, request=request)
    wrapper.aresults = AsyncMock(side_effect=httpx.HTTPStatusError(
        f"status {status_code}", request=request, response=response,
    ))

    with patch(
        'openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context'
    ) as mock_ctx, patch.object(qps_rate_limiter, "acquire", new=AsyncMock()):
        mock_ctx.get.return_value = {"tavily": wrapper}
        result = await run_web_search("current news", "tavily")

    assert result["retryable"] is True


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

    @pytest.mark.asyncio
    async def test_tavily_bounded_search_uses_one_provider_request(self):
        """带结束日期的 Tavily 搜索只发送一次完整时间窗请求。"""
        wrapper = TavilySearchAPIWrapper(
            search_api_key=bytearray(b"fake_api_key"),
            search_url=SecretStr("https://api.example.com"),
            end_date="2021-01-01",
        )
        raw_search = AsyncMock(return_value={
            "results": [{"title": "Bounded", "url": "https://example.com/bounded", "content": "bounded"}]
        })

        with patch.object(wrapper, "raw_search_results_async", new=raw_search), patch.object(
                qps_rate_limiter, "acquire", new=AsyncMock()
        ) as acquire, patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context'
        ) as mock_ctx:
            mock_ctx.get.return_value = {"tavily": wrapper}
            result = await run_web_search("historical query", "tavily")

        assert [item["title"] for item in result["search_results"]] == ["Bounded"]
        assert raw_search.await_count == 1
        assert acquire.await_count == 1


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


class TestWebSearchTemporalScope:
    """Tavily 会话级时间范围测试。"""

    def test_source_date_end_only_configures_tavily_time_window(self):
        """仅有结束边界时也应配置严格上界，避免范围外结果占满 top-K。"""
        mock_wrapper = Mock(start_date=None, end_date=None)
        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = {"tavily": mock_wrapper}

            applied = apply_web_search_temporal_scope(
                "tavily",
                {
                    "constraint_type": "source_date",
                    "end_date": "2023-12-31",
                },
            )

        assert applied is True
        assert mock_wrapper.start_date is None
        assert mock_wrapper.end_date == "2024-01-01"

    @pytest.mark.parametrize(
        ("scope", "expected_start"),
        [
            ({"constraint_type": "source_date", "start_date": "0001-01-01"}, None),
            ({"constraint_type": "source_date", "end_date": "9999-12-31"}, None),
        ],
    )
    def test_extreme_temporal_boundaries_do_not_overflow(self, scope, expected_start):
        """日期极值应安全退化为不下推该侧边界，而不是抛出 OverflowError。"""
        mock_wrapper = Mock(start_date="old", end_date="old")
        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = {"tavily": mock_wrapper}

            applied = apply_web_search_temporal_scope("tavily", scope)

        assert applied is True
        assert mock_wrapper.start_date == expected_start
        assert mock_wrapper.end_date is None

    @pytest.mark.parametrize("scope", [None, {"constraint_type": "content_date", "end_date": "2023-12-31"}])
    def test_non_source_scope_clears_native_date_params(self, scope):
        """无约束和内容时间均不能向 Tavily 发送来源日期参数。"""
        mock_wrapper = Mock(start_date="2020-01-01", end_date="2024-01-01")
        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = {"tavily": mock_wrapper}

            applied = apply_web_search_temporal_scope("tavily", scope)

        assert applied is True
        assert mock_wrapper.start_date is None
        assert mock_wrapper.end_date is None

    def test_unsupported_engine_is_not_mutated(self):
        """仅 Tavily 支持原生日期参数。"""
        mock_wrapper = Mock()
        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.web_search.web_search_context') as mock_ctx:
            mock_ctx.get.return_value = {"google": mock_wrapper}

            applied = apply_web_search_temporal_scope(
                "google",
                {"constraint_type": "source_date", "end_date": "2023-12-31"},
            )

        assert applied is False

    def test_temporal_scope_is_isolated_between_web_search_contexts(self):
        """一个会话的 Tavily 日期范围不能泄漏到另一个 wrapper 实例。"""
        first_wrapper = Mock(start_date=None, end_date=None)
        second_wrapper = Mock(start_date=None, end_date=None)
        first_token = web_search_context.set({"tavily": first_wrapper})
        try:
            apply_web_search_temporal_scope(
                "tavily",
                {"constraint_type": "source_date", "start_date": "2020-01-01"},
            )
        finally:
            web_search_context.reset(first_token)

        second_token = web_search_context.set({"tavily": second_wrapper})
        try:
            assert second_wrapper.start_date is None
            assert second_wrapper.end_date is None
        finally:
            web_search_context.reset(second_token)

        assert first_wrapper.start_date == "2019-12-31"
