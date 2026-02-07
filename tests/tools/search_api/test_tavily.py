import json
from unittest.mock import Mock, patch, AsyncMock

import pytest
from pydantic import SecretStr


class TestTavilySearchAPIWrapper:
    """TavilySearchAPIWrapper 单元测试"""

    @pytest.fixture
    def wrapper(self):
        """创建测试用的wrapper实例"""
        # 修正：使用真实的 SecretStr 实例而不是 Mock
        from jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper import TavilySearchAPIWrapper

        # 创建实际的 SecretStr 实例
        secret_str = SecretStr("http://api.example.com")

        # 打补丁 get_secret_value 方法
        with patch.object(secret_str, 'get_secret_value', return_value="http://api.example.com"):
            wrapper = TavilySearchAPIWrapper[str](
                search_api_key=bytearray(b'fake_api_key'),
                search_url=secret_str,
                max_web_search_results=5
            )
            return wrapper

    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.requests.post')
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.SslUtils.get_ssl_config')
    def test_raw_search_results(self, mock_get_ssl_config, mock_post, wrapper):
        """测试 raw_search_results 方法"""
        # 模拟SSL配置
        mock_get_ssl_config.return_value = (True, None)

        # 模拟响应
        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Test Result", "url": "http://example.com", "content": "Test content"}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        # 调用方法前需要打补丁 get_secret_value
        with patch.object(wrapper.search_url, 'get_secret_value', return_value="http://api.example.com"):
            # 调用方法
            result = wrapper.raw_search_results(
                query="test query",
                max_results=3,
                search_depth="basic",
                include_domains=["example.com"],
                exclude_domains=["bad.com"],
                include_answer=True,
                include_raw_content=True,
                include_images=False
            )

        # 验证结果
        assert result == mock_response.json.return_value
        mock_post.assert_called_once()

        # 验证调用参数
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://api.example.com/search"
        assert call_args[1]['json']['query'] == "test query"
        assert call_args[1]['json']['max_results'] == 3

    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.requests.post')
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.SslUtils.get_ssl_config')
    def test_results(self, mock_get_ssl_config, mock_post, wrapper):
        """测试 results 方法"""
        # 模拟SSL配置
        mock_get_ssl_config.return_value = (False, None)

        # 模拟响应
        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Test Result" * 100,  # 长标题
                    "url": "http://example.com/" + "a" * 3000,  # 长URL
                    "content": "Test content" * 1000,  # 长内容
                    "score": 0.95,
                    "raw_content": "Raw content here"
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        # 调用方法前需要打补丁 get_secret_value
        with patch.object(wrapper.search_url, 'get_secret_value', return_value="http://api.example.com"):
            # 调用方法
            results = wrapper.results(
                query="test query",
                search_depth="advanced",
                include_domains=None,  # 测试None值
                exclude_domains=["bad.com"],
                include_answer=True,
                include_raw_content=False,
                include_images=True
            )

        # 验证结果被正确清理
        assert len(results) == 1
        # 注意：这里需要导入或定义这些常量
        from jiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH, MAX_SEARCH_CONTENT_LENGTH
        assert len(results[0]['title']) <= MAX_SEARCH_CONTENT_LENGTH
        assert len(results[0]['url']) <= MAX_URL_LENGTH
        assert len(results[0]['content']) <= MAX_SEARCH_CONTENT_LENGTH
        assert results[0]['score'] == 0.95
        assert 'raw_content' in results[0]  # raw_content应该保留

    def test_clean_results(self, wrapper):
        """测试 clean_results 方法"""
        # 导入常量
        from jiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH, MAX_SEARCH_CONTENT_LENGTH

        test_results = [
            {
                "title": "A" * (MAX_SEARCH_CONTENT_LENGTH + 1),  # 超过限制
                "url": "http://example.com/" + "b" * (MAX_URL_LENGTH + 1),  # 超过限制
                "content": "C" * (MAX_SEARCH_CONTENT_LENGTH + 1),  # 超过限制
                "score": 0.8,
                "raw_content": "Some raw content"
            },
            {
                "title": "Short title",
                "url": "http://short.com",
                "content": "Short content",
                "score": 0.5
                # 没有raw_content
            }
        ]

        cleaned = wrapper.clean_results(test_results)

        # 验证清理结果
        assert len(cleaned) == 2

        # 第一个结果应该被截断
        assert len(cleaned[0]['title']) == MAX_SEARCH_CONTENT_LENGTH
        assert len(cleaned[0]['url']) == MAX_URL_LENGTH
        assert len(cleaned[0]['content']) == MAX_SEARCH_CONTENT_LENGTH
        assert cleaned[0]['score'] == 0.8
        assert cleaned[0]['raw_content'] == "Some raw content"

        # 第二个结果不变（除了可能添加的raw_content）
        assert cleaned[1]['title'] == "Short title"
        assert 'raw_content' not in cleaned[1]  # 没有raw_content时不应该添加

    @pytest.mark.asyncio
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.httpx.AsyncClient')
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.SslUtils.get_ssl_config')
    async def test_raw_search_results_async(self, mock_get_ssl_config, mock_async_client, wrapper):
        """测试 raw_search_results_async 方法"""
        # 模拟SSL配置
        mock_get_ssl_config.return_value = (True, "/path/to/cert")

        # 模拟异步客户端
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({
            "results": [{"title": "Async Result"}],
            "other": "data"
        })
        mock_client.post.return_value = mock_response
        mock_async_client.return_value.__aenter__.return_value = mock_client

        # 调用异步方法前需要打补丁 get_secret_value
        with patch.object(wrapper.search_url, 'get_secret_value', return_value="http://api.example.com"):
            # 调用异步方法
            result = await wrapper.raw_search_results_async(
                query="async query",
                max_results=2,
                search_depth="basic"
            )

        # 验证结果
        assert result["results"][0]["title"] == "Async Result"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.httpx.AsyncClient')
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.SslUtils.get_ssl_config')
    async def test_aresults(self, mock_get_ssl_config, mock_async_client, wrapper):
        """测试 aresults 方法"""
        # 模拟SSL配置
        mock_get_ssl_config.return_value = (False, None)

        # 模拟异步客户端
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({
            "results": [
                {
                    "title": "Async Cleaned",
                    "url": "http://async.com",
                    "content": "Async content",
                    "score": 0.75
                }
            ]
        })
        mock_client.post.return_value = mock_response
        mock_async_client.return_value.__aenter__.return_value = mock_client

        # 调用异步方法前需要打补丁 get_secret_value
        with patch.object(wrapper.search_url, 'get_secret_value', return_value="http://api.example.com"):
            # 调用异步方法
            results = await wrapper.aresults(
                query="async clean query",
                search_depth="advanced",
                include_domains=["good.com"],
                exclude_domains=None,
                include_answer=False,
                include_raw_content=True,
                include_images=False
            )

        # 验证清理后的结果
        assert len(results) == 1
        assert results[0]['title'] == "Async Cleaned"
        assert results[0]['score'] == 0.75

    @pytest.mark.asyncio
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.httpx.AsyncClient')
    @patch('jiuwen_deepsearch.framework.jiuwen.tools.Search_API.tavily.api_wrapper.SslUtils.get_ssl_config')
    async def test_raw_search_results_async_error(self, mock_get_ssl_config, mock_async_client, wrapper):
        """测试 raw_search_results_async 方法错误情况"""
        # 模拟SSL配置
        mock_get_ssl_config.return_value = (True, None)

        # 模拟错误响应
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        mock_client.post.return_value = mock_response
        mock_async_client.return_value.__aenter__.return_value = mock_client

        # 调用异步方法前需要打补丁 get_secret_value
        with patch.object(wrapper.search_url, 'get_secret_value', return_value="http://api.example.com"):
            # 验证抛出异常
            with pytest.raises(Exception, match="Error 500: Internal Server Error"):
                await wrapper.raw_search_results_async(query="error query")
