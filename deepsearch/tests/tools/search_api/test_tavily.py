import json
from unittest.mock import Mock, patch, AsyncMock

import pytest
from pydantic import SecretStr


class TestTavilySearchAPIWrapper:
    """TavilySearchAPIWrapper 单元测试"""

    @pytest.fixture
    def wrapper(self):
        """创建测试用的wrapper实例"""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        secret_str = SecretStr("http://api.example.com")

        with patch.object(secret_str, "get_secret_value", return_value="http://api.example.com"):
            return TavilySearchAPIWrapper[str](
                search_api_key=bytearray(b"fake_api_key"),
                search_url=secret_str,
                max_web_search_results=5,
            )

    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.requests.post")
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.SslUtils.get_ssl_config")
    def test_raw_search_results(self, mock_get_ssl_config, mock_post):
        """测试 raw_search_results 方法"""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        mock_get_ssl_config.return_value = (True, None)

        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Test Result", "url": "http://example.com", "content": "Test content"}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        secret_str = SecretStr("http://api.example.com")
        with patch.object(secret_str, "get_secret_value", return_value="http://api.example.com"):
            wrapper = TavilySearchAPIWrapper[str](
                search_api_key=bytearray(b"fake_api_key"),
                search_url=secret_str,
                max_web_search_results=3,
                search_depth="basic",
                include_domains=["example.com"],
                exclude_domains=["bad.com"],
                include_answer=True,
                include_raw_content=True,
                include_images=False,
            )

        with patch.object(wrapper.search_url, "get_secret_value", return_value="http://api.example.com"):
            result = wrapper.raw_search_results(query="test query")

        assert result == mock_response.json.return_value
        mock_post.assert_called_once()

        call_args = mock_post.call_args
        assert call_args[0][0] == "http://api.example.com/search"
        assert call_args[1]["json"]["query"] == "test query"
        assert call_args[1]["json"]["max_results"] == 3

    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.requests.post")
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.SslUtils.get_ssl_config")
    def test_raw_search_results_uses_default_url_when_empty(self, mock_get_ssl_config, mock_post):
        """Tavily should use its public default endpoint when search_url is empty."""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        mock_get_ssl_config.return_value = (False, None)
        mock_response = Mock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        wrapper = TavilySearchAPIWrapper(
            search_api_key=bytearray(b"fake_api_key"),
            search_url="",
            max_web_search_results=3,
        )

        wrapper.raw_search_results(query="test query")

        assert mock_post.call_args[0][0] == "https://api.tavily.com/search"

    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.requests.post")
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.SslUtils.get_ssl_config")
    def test_results(self, mock_get_ssl_config, mock_post):
        """测试 results 方法"""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        mock_get_ssl_config.return_value = (False, None)

        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Test Result" * 100,
                    "url": "http://example.com/" + "a" * 3000,
                    "content": "Test content" * 1000,
                    "score": 0.95,
                    "raw_content": "Raw content here",
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        secret_str = SecretStr("http://api.example.com")
        with patch.object(secret_str, "get_secret_value", return_value="http://api.example.com"):
            wrapper = TavilySearchAPIWrapper[str](
                search_api_key=bytearray(b"fake_api_key"),
                search_url=secret_str,
                max_web_search_results=5,
                search_depth="advanced",
                include_domains=None,
                exclude_domains=["bad.com"],
                include_answer=True,
                include_raw_content=False,
                include_images=True,
            )

        with patch.object(wrapper.search_url, "get_secret_value", return_value="http://api.example.com"):
            results = wrapper.results(query="test query")

        assert len(results) == 1
        from openjiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH, MAX_SEARCH_CONTENT_LENGTH

        assert len(results[0]["title"]) <= MAX_SEARCH_CONTENT_LENGTH
        assert len(results[0]["url"]) <= MAX_URL_LENGTH
        assert len(results[0]["content"]) <= MAX_SEARCH_CONTENT_LENGTH
        assert results[0]["score"] == 0.95
        assert "raw_content" in results[0]

    def test_clean_results(self, wrapper):
        """测试 clean_results 方法"""
        from openjiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH, MAX_SEARCH_CONTENT_LENGTH

        test_results = [
            {
                "title": "A" * (MAX_SEARCH_CONTENT_LENGTH + 1),
                "url": "http://example.com/" + "b" * (MAX_URL_LENGTH + 1),
                "content": "C" * (MAX_SEARCH_CONTENT_LENGTH + 1),
                "score": 0.8,
                "raw_content": "Some raw content",
                "published_date": "2020-01-02T03:04:05Z",
                "updated_at": "2022-01-02T03:04:05Z",
            },
            {
                "title": "Short title",
                "url": "http://short.com",
                "content": "Short content",
                "score": 0.5,
            },
        ]

        cleaned = wrapper.clean_results(test_results)

        assert len(cleaned) == 2

        assert len(cleaned[0]["title"]) == MAX_SEARCH_CONTENT_LENGTH
        assert len(cleaned[0]["url"]) == MAX_URL_LENGTH
        assert len(cleaned[0]["content"]) == MAX_SEARCH_CONTENT_LENGTH
        assert cleaned[0]["score"] == 0.8
        assert cleaned[0]["raw_content"] == "Some raw content"
        assert cleaned[0]["source_date"] == "2020-01-02"
        assert cleaned[0]["source_date_type"] == "published"
        assert "published_date" not in cleaned[0]
        assert "updated_at" not in cleaned[0]

        assert cleaned[1]["title"] == "Short title"
        assert "raw_content" not in cleaned[1]

    def test_clean_results_normalizes_official_rfc2822_publication_date(self, wrapper):
        """Tavily 官方 news 日期格式应在 provider 边界归一化为 ISO 日期。"""
        cleaned = wrapper.clean_results([
            {
                "title": "News",
                "url": "https://example.com/news",
                "content": "body",
                "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
            }
        ])

        assert cleaned[0]["source_date"] == "2025-03-11"
        assert cleaned[0]["source_date_type"] == "published"

    @pytest.mark.asyncio
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.httpx.AsyncClient")
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.SslUtils.get_ssl_config")
    async def test_raw_search_results_async(self, mock_get_ssl_config, mock_async_client):
        """测试 raw_search_results_async 方法"""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        mock_get_ssl_config.return_value = (True, "/path/to/cert")

        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(
            {
                "results": [{"title": "Async Result"}],
                "other": "data",
            }
        )
        mock_client.post.return_value = mock_response
        mock_async_client.return_value.__aenter__.return_value = mock_client

        secret_str = SecretStr("http://api.example.com")
        with patch.object(secret_str, "get_secret_value", return_value="http://api.example.com"):
            wrapper = TavilySearchAPIWrapper[str](
                search_api_key=bytearray(b"fake_api_key"),
                search_url=secret_str,
                max_web_search_results=2,
                search_depth="basic",
            )

        with patch.object(wrapper.search_url, "get_secret_value", return_value="http://api.example.com"):
            result = await wrapper.raw_search_results_async(query="async query")

        assert result["results"][0]["title"] == "Async Result"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.httpx.AsyncClient")
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.SslUtils.get_ssl_config")
    async def test_aresults(self, mock_get_ssl_config, mock_async_client):
        """测试 aresults 方法"""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        mock_get_ssl_config.return_value = (False, None)

        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(
            {
                "results": [
                    {
                        "title": "Async Cleaned",
                        "url": "http://async.com",
                        "content": "Async content",
                        "score": 0.75,
                    }
                ]
            }
        )
        mock_client.post.return_value = mock_response
        mock_async_client.return_value.__aenter__.return_value = mock_client

        secret_str = SecretStr("http://api.example.com")
        with patch.object(secret_str, "get_secret_value", return_value="http://api.example.com"):
            wrapper = TavilySearchAPIWrapper[str](
                search_api_key=bytearray(b"fake_api_key"),
                search_url=secret_str,
                max_web_search_results=5,
                search_depth="advanced",
                include_domains=["good.com"],
                exclude_domains=None,
                include_answer=False,
                include_raw_content=True,
                include_images=False,
            )

        with patch.object(wrapper.search_url, "get_secret_value", return_value="http://api.example.com"):
            results = await wrapper.aresults(query="async clean query")

        assert len(results) == 1
        assert results[0]["title"] == "Async Cleaned"
        assert results[0]["score"] == 0.75

    @pytest.mark.asyncio
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.httpx.AsyncClient")
    @patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper.SslUtils.get_ssl_config")
    async def test_raw_search_results_async_error(self, mock_get_ssl_config, mock_async_client, wrapper):
        """测试 raw_search_results_async 方法错误情况"""
        mock_get_ssl_config.return_value = (True, None)

        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        mock_client.post.return_value = mock_response
        mock_async_client.return_value.__aenter__.return_value = mock_client

        with patch.object(wrapper.search_url, "get_secret_value", return_value="http://api.example.com"):
            with pytest.raises(Exception, match="Error 500: Internal Server Error"):
                await wrapper.raw_search_results_async(query="error query")

    def test_extension_overrides_search_options(self):
        """extension 中的 Tavily 配置应在 model_post_init 中应用到实例字段"""
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        secret_str = SecretStr("http://api.example.com")
        wrapper = TavilySearchAPIWrapper(
            search_api_key=bytearray(b"k"),
            search_url=secret_str,
            extension={
                "search_depth": "basic",
                "include_domains": ["a.com"],
                "include_answer": True,
            },
        )
        assert wrapper.search_depth == "basic"
        assert wrapper.include_domains == ["a.com"]
        assert wrapper.include_answer is True
        # 未在 extension 中出现的字段保持类默认值
        assert wrapper.include_images is False

    def test_extension_none_and_empty_dict_use_defaults(self):
        from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.tavily.api_wrapper import (
            TavilySearchAPIWrapper,
        )

        secret_str = SecretStr("http://api.example.com")
        w1 = TavilySearchAPIWrapper(
            search_api_key=bytearray(b"k"),
            search_url=secret_str,
            extension=None,
        )
        assert w1.search_depth == "advanced"

        w2 = TavilySearchAPIWrapper(
            search_api_key=bytearray(b"k"),
            search_url=secret_str,
            extension={},
        )
        assert w2.search_depth == "advanced"

    def test_build_search_params_includes_only_configured_absolute_dates(self, wrapper):
        """Tavily 请求体仅应携带已设置的绝对日期边界。"""
        params_without_dates = wrapper._build_search_params("baseline")
        wrapper.start_date = "2019-12-31"
        params_with_start = wrapper._build_search_params("bounded")
        wrapper.end_date = "2024-01-01"
        params_with_both = wrapper._build_search_params("bounded")

        assert "start_date" not in params_without_dates
        assert "end_date" not in params_without_dates
        assert params_with_start["start_date"] == "2019-12-31"
        assert "end_date" not in params_with_start
        assert params_with_both["start_date"] == "2019-12-31"
        assert params_with_both["end_date"] == "2024-01-01"
        assert "time_range" not in params_with_both

    def test_clean_results_truncates_large_raw_content(self, wrapper):
        """测试 clean_results 对超大 raw_content 的截断"""
        from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH

        # 创建超大的 raw_content（大于 MAX_SEARCH_CONTENT_LENGTH）
        large_raw_content = "R" * (MAX_SEARCH_CONTENT_LENGTH + 200000)

        test_results = [
            {
                "title": "Test Title",
                "url": "http://example.com/test",
                "content": "Test Content",
                "score": 0.9,
                "raw_content": large_raw_content,
            }
        ]

        cleaned = wrapper.clean_results(test_results)

        # 验证 raw_content 被截断到 MAX_SEARCH_CONTENT_LENGTH
        assert len(cleaned) == 1
        assert "raw_content" in cleaned[0]
        assert len(cleaned[0]["raw_content"]) == MAX_SEARCH_CONTENT_LENGTH
        assert cleaned[0]["raw_content"] == large_raw_content[:MAX_SEARCH_CONTENT_LENGTH]

    def test_clean_results_handles_malicious_endpoint_large_raw_content(self, wrapper):
        """测试恶意 endpoint 返回超大 raw_content 的场景（CVE-400 资源消耗漏洞）"""
        from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH

        # 模拟恶意 endpoint 返回超大 raw_content（即使未请求也会返回）
        malicious_raw_content = "MALICIOUS" * 150000  # 约 1,200,000 字符

        test_results = [
            {
                "title": "Malicious Page",
                "url": "http://attacker-controlled.example/page",
                "content": "Malicious content",
                "score": 0.5,
                "raw_content": malicious_raw_content,  # 恶意 endpoint 强制返回超大 raw_content
            }
        ]

        cleaned = wrapper.clean_results(test_results)

        # 验证即使恶意 endpoint 返回超大 raw_content，也被截断
        assert len(cleaned) == 1
        assert "raw_content" in cleaned[0]
        assert len(cleaned[0]["raw_content"]) == MAX_SEARCH_CONTENT_LENGTH
        assert len(cleaned[0]["raw_content"]) < len(malicious_raw_content)

        # 验证截断后的内容长度符合预期
        expected_max = MAX_SEARCH_CONTENT_LENGTH
        actual_len = len(cleaned[0]["raw_content"])
        assert actual_len == expected_max, f"raw_content 应被截断到 {expected_max}, 实际为 {actual_len}"
