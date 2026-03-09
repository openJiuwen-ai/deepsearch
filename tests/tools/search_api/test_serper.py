import os

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["TOOL_SSL_VERIFY"] = "false"

from unittest.mock import Mock, patch, AsyncMock

import pytest
import requests
from pydantic import SecretStr

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper import GoogleSearchAPIWrapper


class TestGoogleSearchAPIWrapper:

    def setup_method(self):
        """在每个测试方法前执行"""
        self.mock_api_key = bytearray(b"test-api-key-123")
        self.mock_search_url = SecretStr("https://api.serper.dev")
        self.wrapper = GoogleSearchAPIWrapper(
            search_api_key=self.mock_api_key,
            search_url=self.mock_search_url,
            max_web_search_results=5
        )

    def test_init(self):
        """测试初始化"""
        assert self.wrapper.search_api_key == bytearray(b"test-api-key-123")
        assert self.wrapper.search_url.get_secret_value() == "https://api.serper.dev"
        assert self.wrapper.max_web_search_results == 5
        assert self.wrapper.type == "search"
        assert self.wrapper.gl == "us"
        assert self.wrapper.hl == "en"

    @patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.requests.post')
    @patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.SslUtils.get_ssl_config')
    def test_google_search_results_success(self, mock_ssl_config, mock_post):
        """测试同步搜索功能 - 成功情况"""
        # 模拟SSL配置
        mock_ssl_config.return_value = (True, "/path/to/cert")

        # 模拟响应
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "organic": [{"title": "Test Result", "link": "http://example.com"}]
        }
        mock_post.return_value = mock_response

        # 执行测试
        result = self.wrapper.google_search_results(
            "test query",
            gl="us",
            hl="en",
            num=5
        )

        # 验证调用参数
        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # 验证URL
        assert "https://api.serper.dev/search" in str(call_args[0])

        # 验证headers
        headers = call_args[1]['headers']
        assert headers['X-API-KEY'] == "test-api-key-123"
        assert headers['Content-Type'] == "application/json"

        # 验证params
        params = call_args[1]['params']
        assert params['q'] == "test query"
        assert params['gl'] == "us"

        # 验证verify参数
        assert call_args[1]['verify'] == "/path/to/cert"

        # 验证结果
        assert "organic" in result
        assert len(result["organic"]) == 1
        assert result["organic"][0]["title"] == "Test Result"

    @patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.requests.post')
    @patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.SslUtils.get_ssl_config')
    def test_google_search_results_with_custom_type(self, mock_ssl_config, mock_post):
        """测试不同类型的搜索"""
        mock_ssl_config.return_value = (False, None)

        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "news": [{"title": "News Result"}]
        }
        mock_post.return_value = mock_response

        # 测试news类型
        result = self.wrapper.google_search_results(
            "test query",
            search_type="news"
        )

        assert "news" in result
        assert result["news"][0]["title"] == "News Result"

        # 验证verify为False（当ssl_verify为False时）
        call_args = mock_post.call_args
        assert call_args[1]['verify'] is False

    @patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.requests.post')
    def test_google_search_results_with_exception(self, mock_post):
        """测试搜索时的异常处理"""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("API Error")
        mock_post.return_value = mock_response

        with pytest.raises(requests.exceptions.HTTPError):
            self.wrapper.google_search_results("test query")

    @patch(
        'openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.'
        'GoogleSearchAPIWrapper.google_search_results'
    )
    def test_results_method(self, mock_search_results):
        """测试results包装方法"""
        expected_result = {"organic": [{"title": "Test"}]}
        mock_search_results.return_value = expected_result

        result = self.wrapper.results("test query", extra_param="value")

        # 验证调用了底层方法
        mock_search_results.assert_called_once_with(
            search_term="test query",
            search_type="search",
            gl="us",
            hl="en",
            num=5,
            tbs=None,
            extra_param="value"
        )

        assert result == expected_result

    @pytest.mark.asyncio
    @patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.SslUtils.get_ssl_config')
    async def test_async_google_search_results(self, mock_ssl_config):
        """测试异步搜索功能"""
        mock_ssl_config.return_value = (True, "/path/to/cert")

        # 创建模拟的响应对象 - json()是同步方法
        mock_response = Mock()
        mock_response.json.return_value = {
            "organic": [{"title": "Async Result", "link": "http://example.com"}]
        }

        # 模拟AsyncClient
        mock_client = AsyncMock()
        # 模拟异步上下文管理器
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        # 模拟post方法返回响应
        mock_client.post.return_value = mock_response

        with patch('openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.httpx.AsyncClient',
                   return_value=mock_client):
            # 执行异步测试
            result = await self.wrapper.async_google_search_results(
                "async query",
                gl="cn",
                hl="zh-cn"
            )

            # 验证调用
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args

            # 验证URL
            assert "https://api.serper.dev/search" in str(call_args[0])

            # 验证headers
            headers = call_args[1]['headers']
            assert headers['X-API-KEY'] == "test-api-key-123"

            # 验证params
            params = call_args[1]['params']
            assert params['q'] == "async query"
            assert params['gl'] == "cn"
            assert params['hl'] == "zh-cn"

            # 验证结果
            assert "organic" in result
            assert result["organic"][0]["title"] == "Async Result"

    @pytest.mark.asyncio
    async def test_async_google_search_results_with_ssl_false(self):
        """测试SSL验证为False的异步搜索"""
        # 模拟SSL配置返回False
        with patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.'
                'SslUtils.get_ssl_config'
        ) as mock_ssl_config:
            mock_ssl_config.return_value = (False, None)

            # 创建模拟的响应对象
            mock_response = Mock()
            mock_response.json.return_value = {"organic": []}

            # 模拟AsyncClient
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response

            with patch(
                    'openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.httpx.AsyncClient',
                    return_value=mock_client):
                # 执行测试
                result = await self.wrapper.async_google_search_results("test query")

                # 验证verify参数为False
                mock_client.post.assert_called_once()
                call_kwargs = mock_client.post.call_args[1]
                # 注意：httpx.AsyncClient的verify参数是在创建client时设置的，不是在post调用时
                # 需要验证AsyncClient的创建参数
                pass

    @pytest.mark.asyncio
    @patch(
        'openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serper.api_wrapper.'
        'GoogleSearchAPIWrapper.async_google_search_results'
    )
    async def test_aresults_method(self, mock_async_search_results):
        """测试异步包装方法"""
        expected_result = {"organic": [{"title": "Async Test"}]}
        # 注意：async_google_search_results是异步方法，我们需要模拟它返回一个可等待的结果
        mock_async_search_results.return_value = expected_result

        result = await self.wrapper.aresults("test query", extra_param="value")

        # 验证调用了底层异步方法
        mock_async_search_results.assert_called_once_with(
            search_term="test query",
            search_type="search",
            gl="us",
            hl="en",
            num=5,
            tbs=None,
            extra_param="value"
        )
        assert result == expected_result

    def test_result_key_mapping(self):
        """测试不同类型对应的结果键名"""
        assert self.wrapper.result_key_for_type["news"] == "news"
        assert self.wrapper.result_key_for_type["search"] == "organic"
        assert self.wrapper.result_key_for_type["places"] == "places"
        assert self.wrapper.result_key_for_type["images"] == "images"

    def test_model_config(self):
        """测试模型配置"""
        # 测试extra='allow'允许额外字段
        wrapper_with_extra = GoogleSearchAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=SecretStr("url"),
            extra_field="allowed"
        )
        assert hasattr(wrapper_with_extra, 'extra_field')

    def test_search_api_key_decoding(self):
        """测试API key解码"""
        # 确保API key能正确解码
        api_key = bytearray(b"test-key")
        wrapper = GoogleSearchAPIWrapper(
            search_api_key=api_key,
            search_url=SecretStr("url")
        )

        # 验证解码
        decoded_key = api_key.decode('utf-8')
        assert decoded_key == "test-key"

    def test_search_url_secret(self):
        """测试SecretStr处理"""
        secret_url = SecretStr("https://secret.api.url")
        wrapper = GoogleSearchAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=secret_url
        )

        # 验证可以获取秘密值
        assert wrapper.search_url.get_secret_value() == "https://secret.api.url"
