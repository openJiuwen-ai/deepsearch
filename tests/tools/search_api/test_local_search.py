import pytest
import aiohttp
import requests
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from pydantic import SecretStr
from jiuwen_deepsearch.framework.jiuwen.tools.Search_API.local_search_api.api_wrapper import LocalDatasetAPIWrapper

module_path = "jiuwen_deepsearch.framework.jiuwen.tools.Search_API.local_search_api.api_wrapper"

class TestLocalDatasetAPIWrapper:
    """测试 LocalDatasetAPIWrapper 类"""

    def setup_method(self):
        """每个测试方法运行前都会执行"""
        self.search_api_key = bytearray(b"test_local_api_key_123")
        self.search_url = SecretStr("https://api.localdataset.com/search")
        self.search_datasets = ["dataset_1", "dataset_2", "dataset_3"]
        self.max_local_search_results = 5
        self.recall_threshold = 0.7

        # 创建测试实例
        self.wrapper = LocalDatasetAPIWrapper(
            search_api_key=self.search_api_key,
            search_url=self.search_url,
            search_datasets=self.search_datasets,
            max_local_search_results=self.max_local_search_results,
            recall_threshold=self.recall_threshold
        )

    def test_initialization(self):
        """测试类初始化"""
        assert self.wrapper.search_api_key == self.search_api_key
        assert self.wrapper.search_url.get_secret_value() == "https://api.localdataset.com/search"
        assert self.wrapper.search_datasets == ["dataset_1", "dataset_2", "dataset_3"]
        assert self.wrapper.max_local_search_results == 5
        assert self.wrapper.recall_threshold == 0.7
        assert self.wrapper.extension is None

    def test_initialization_default_values(self):
        """测试默认值初始化"""
        wrapper = LocalDatasetAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=SecretStr("https://example.com")
        )

        assert wrapper.search_datasets == []
        assert wrapper.max_local_search_results == 5
        assert wrapper.recall_threshold == 0.5

    def test_results_method(self):
        """测试同步results方法"""
        mock_results = [
            {"content": "Result 1", "similarity": 0.9},
            {"content": "Result 2", "similarity": 0.8}
        ]

        with patch.object(self.wrapper, '_search_api_results') as mock_search:
            mock_search.return_value = mock_results

            result = self.wrapper.results("test query")

            mock_search.assert_called_once_with("test query", num=5)
            assert result == mock_results

    @pytest.mark.asyncio
    async def test_aresults_method(self):
        """测试异步aresults方法"""
        mock_results = [
            {"content": "Async Result 1", "similarity": 0.95},
            {"content": "Async Result 2", "similarity": 0.85}
        ]

        with patch.object(self.wrapper, '_async_search_api_results') as mock_async_search:
            mock_async_search.return_value = mock_results

            result = await self.wrapper.aresults("test query")

            mock_async_search.assert_called_once_with("test query", num=5)
            assert result == mock_results

    def test_build_headers(self):
        """测试构建请求头和数据"""
        headers = self.wrapper.build_headers()

        # 验证headers
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_build_headers_empty_datasets(self):
        """测试构建空数据集的请求头"""
        wrapper = LocalDatasetAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=SecretStr("https://example.com"),
            search_datasets=[]
        )

        body_params, query_params = wrapper.build_request_params("test query")

        assert body_params["query"] == "test query"
        assert query_params["top_k"] == 5
        assert query_params["recall_threshold"] == 0.5

    def test_build_headers_custom_threshold(self):
        """测试自定义相似度阈值"""
        wrapper = LocalDatasetAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=SecretStr("https://example.com"),
            recall_threshold=0.9  # 自定义阈值
        )

        _, query_params = wrapper.build_request_params("test query")

        assert query_params["recall_threshold"] == 0.9

    def test_search_api_results_success(self):
        """测试同步搜索API成功情况"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "output_list": [
                {"content": "Result 1", "similarity": 0.95, "metadata": {"source": "doc1"}},
                {"content": "Result 2", "similarity": 0.85, "metadata": {"source": "doc2"}},
                {"content": "Result 3", "similarity": 0.75, "metadata": {"source": "doc3"}},
                {"content": "Result 4", "similarity": 0.65, "metadata": {"source": "doc4"}},
                {"content": "Result 5", "similarity": 0.55, "metadata": {"source": "doc5"}}
            ]
        }
        mock_response.raise_for_status.return_value = None

        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config:
            mock_build_headers.return_value = {"X-Auth-Token": "key", "Content-Type": "application/json"}
            mock_post.return_value = mock_response
            mock_ssl_config.return_value = (True, "/path/to/cert")

            result = self.wrapper._search_api_results("test query", num=3)  # 请求3个结果

            # 验证结果被正确截断
            assert len(result) == 3
            assert result[0]["content"] == "Result 1"
            assert result[1]["content"] == "Result 2"
            assert result[2]["content"] == "Result 3"

    def test_search_api_results_ssl_verify_false(self):
        """测试SSL验证关闭的情况"""
        mock_response = Mock()
        mock_response.json.return_value = {"retrieve_result_list": []}
        mock_response.raise_for_status.return_value = None

        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_post.return_value = mock_response
            mock_ssl_config.return_value = (False, None)  # SSL验证关闭

            result = self.wrapper._search_api_results("test query", num=5)

            # 验证verify参数为False
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['verify'] is False

    def test_search_api_results_request_exception(self):
        """测试同步搜索请求异常"""
        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_post.side_effect = requests.exceptions.RequestException("Connection failed")
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = False

            result = self.wrapper._search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证错误日志
            mock_logger.error.assert_called_once_with(
                "Search request failed! Error: Connection failed"
            )

    def test_search_api_results_request_exception_sensitive_mode(self):
        """测试敏感模式下的同步搜索请求异常"""
        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_post.side_effect = requests.exceptions.RequestException("Connection failed")
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = True  # 敏感模式

            result = self.wrapper._search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_once_with("Search request failed!")

    def test_search_api_results_unexpected_response_format(self):
        """测试意外的响应格式"""
        mock_response = Mock()
        mock_response.json.return_value = {'invalid_key': 'unexpected_data'}  # 没有retrieve_result_list键
        mock_response.raise_for_status.return_value = None

        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_post.return_value = mock_response
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = False

            result = self.wrapper._search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []

    def test_search_api_results_non_list_retrieve_result_list(self):
        """测试敏感模式下output_list不是列表的情况"""
        mock_response = Mock()
        mock_response.json.return_value = {'output_list': "not_a_list"}  # 不是列表
        mock_response.raise_for_status.return_value = None

        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_post.return_value = mock_response
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = True  # 敏感模式

            result = self.wrapper._search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_once_with("Unexpected search request response!")

    def test_search_api_results_log_is_not_sensitive(self):
        """测试非敏感模式下output_list不是列表的情况"""
        mock_response = Mock()
        mock_response.json.return_value = {'output_list': "not_a_list"}  # 不是列表
        mock_response.raise_for_status.return_value = None

        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.requests.post") as mock_post, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_post.return_value = mock_response
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = False  # 非敏感模式

            result = self.wrapper._search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_once_with("Unexpected response! original result: not_a_list")

    @pytest.mark.asyncio
    async def test_async_search_api_results_success(self):
        """测试异步搜索成功"""
        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.aiohttp.ClientSession") as MockClientSession, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_ssl_config.return_value = (True, "/path/to/cert")

            # 创建mock响应
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json.return_value = {
                "output_list": [
                    {"content": "Result 1", "similarity": 0.95, "metadata": {"source": "doc1"}},
                    {"content": "Result 2", "similarity": 0.85, "metadata": {"source": "doc2"}},
                    {"content": "Result 3", "similarity": 0.75, "metadata": {"source": "doc3"}},
                    {"content": "Result 4", "similarity": 0.65, "metadata": {"source": "doc4"}},
                    {"content": "Result 5", "similarity": 0.55, "metadata": {"source": "doc5"}}
                ]
            }

            mock_post_context = MagicMock()
            mock_post_context.__aenter__.return_value = mock_response
            mock_post_context.__aexit__.return_value = None

            mock_session = MagicMock()
            mock_session.post.return_value = mock_post_context

            mock_session_context = MagicMock()
            mock_session_context.__aenter__.return_value = mock_session
            mock_session_context.__aexit__.return_value = None

            MockClientSession.return_value = mock_session_context

            result = await self.wrapper._async_search_api_results("test query", num=5)

            # 验证返回列表
            assert len(result) == 5

    @pytest.mark.asyncio
    async def test_async_search_api_results_with_sensitive_client_error(self):
        """测试异步搜索客户端错误"""
        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.aiohttp.ClientSession") as mock_session_class, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = True

            # 模拟ClientSession抛出异常
            mock_session_class.side_effect = aiohttp.ClientError("Connection failed")

            result = await self.wrapper._async_search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证错误日志
            mock_logger.error.assert_called_once_with("Search request failed!")

    @pytest.mark.asyncio
    async def test_async_search_api_results_ssl_false_client_error(self):
        """测试异步搜索客户端错误"""
        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.aiohttp.ClientSession") as mock_session_class, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_ssl_config.return_value = (False, "/path/to/cert")
            mock_sensitive.return_value = False

            # 模拟ClientSession抛出异常
            mock_session_class.side_effect = aiohttp.ClientError("Connection failed")

            result = await self.wrapper._async_search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证错误日志
            mock_logger.error.assert_called_once_with(
                "Search request failed! Error: Connection failed"
            )

    @pytest.mark.asyncio
    async def test_async_search_api_results_client_error(self):
        """测试异步搜索客户端错误"""
        with patch.object(self.wrapper, 'build_headers') as mock_build_headers, \
                patch(f"{module_path}.aiohttp.ClientSession") as mock_session_class, \
                patch(f"{module_path}.SslUtils.get_ssl_config") as mock_ssl_config, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_build_headers.return_value = ({}, "https://api.example.com", {})
            mock_ssl_config.return_value = (True, "/path/to/cert")
            mock_sensitive.return_value = False

            # 模拟ClientSession抛出异常
            mock_session_class.side_effect = aiohttp.ClientError("Connection failed")

            result = await self.wrapper._async_search_api_results("test query", num=5)

            # 验证返回空列表
            assert result == []
            # 验证错误日志
            mock_logger.error.assert_called_once_with(
                "Search request failed! Error: Connection failed"
            )
