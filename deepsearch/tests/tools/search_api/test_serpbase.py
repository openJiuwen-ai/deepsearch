import logging
import os

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["TOOL_SSL_VERIFY"] = "false"

from unittest.mock import Mock, patch, AsyncMock

import pytest
import requests
from pydantic import SecretStr, ValidationError

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serpbase.api_wrapper import (
    DEFAULT_SERPBASE_SEARCH_URL,
    SerpBaseSearchAPIWrapper,
    SerpBaseSearchResponseError,
)

MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.serpbase.api_wrapper"

SUCCESS_PAYLOAD = {
    "status": 0,
    "request_id": "r-b4b27fe8a36d9ac303a5da7dd",
    "elapsed_ms": 412,
    "credits_charged": 1,
    "organic": [
        {
            "title": "Test Result",
            "link": "https://example.com/page",
            "snippet": "A test snippet.",
            "position": 1,
        }
    ],
}


def _mock_response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class TestSerpBaseSearchAPIWrapper:

    def setup_method(self):
        """在每个测试方法前执行"""
        self.mock_api_key = bytearray(b"test-api-key-123")
        self.mock_search_url = SecretStr("https://api.serpbase.dev")
        self.wrapper = SerpBaseSearchAPIWrapper(
            search_api_key=self.mock_api_key,
            search_url=self.mock_search_url,
            max_web_search_results=5
        )

    def test_init(self):
        """测试初始化"""
        assert self.wrapper.search_api_key == bytearray(b"test-api-key-123")
        assert self.wrapper.search_url.get_secret_value() == "https://api.serpbase.dev"
        assert self.wrapper.max_web_search_results == 5
        assert self.wrapper.gl == "us"
        assert self.wrapper.hl == "en"

    @patch(f"{MODULE_PATH}.requests.post")
    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    def test_google_search_results_success(self, mock_ssl_config, mock_post):
        """测试同步搜索功能 - 成功情况"""
        mock_ssl_config.return_value = (True, "/path/to/cert")
        mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)

        result = self.wrapper.google_search_results("test query")

        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # POST 到 Google web search 端点
        assert call_args[0][0] == "https://api.serpbase.dev/google/search"

        # 密钥只走 header，不进查询串
        headers = call_args[1]['headers']
        assert headers['X-API-Key'] == "test-api-key-123"
        assert headers['Content-Type'] == "application/json"
        assert "test-api-key-123" not in str(call_args[0][0])

        # 验证json body
        payload = call_args[1]['json']
        assert payload['q'] == "test query"
        assert payload['gl'] == "us"
        assert payload['hl'] == "en"
        assert payload['num'] == 5

        # 验证verify参数
        assert call_args[1]['verify'] == "/path/to/cert"

        # 验证同步请求不会无限期阻塞
        assert call_args[1]['timeout'] == 30

        # 验证结果（wrapper 返回 organic 列表）
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["title"] == "Test Result"
        assert result[0]["link"] == "https://example.com/page"
        assert result[0]["snippet"] == "A test snippet."

    @patch(f"{MODULE_PATH}.requests.post")
    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    def test_google_search_results_uses_default_url_when_empty(self, mock_ssl_config, mock_post):
        """SerpBase should use the public default endpoint when search_url is empty."""
        mock_ssl_config.return_value = (False, None)
        mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)

        wrapper = SerpBaseSearchAPIWrapper(
            search_api_key=bytearray(b"test-api-key-123"),
            search_url="",
            max_web_search_results=3,
        )

        wrapper.google_search_results("test query")

        call_args = mock_post.call_args
        assert call_args[0][0] == f"{DEFAULT_SERPBASE_SEARCH_URL}/google/search"
        assert call_args[1]["json"] == {
            "q": "test query",
            "gl": "us",
            "hl": "en",
            "num": 3,
        }
        assert call_args[1]["verify"] is False

    @patch(f"{MODULE_PATH}.requests.post")
    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    def test_extension_overrides_gl_and_hl(self, mock_ssl_config, mock_post):
        """测试 extension 中 gl/hl 覆盖默认区域设置"""
        mock_ssl_config.return_value = (False, None)
        mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)

        wrapper = SerpBaseSearchAPIWrapper(
            search_api_key=bytearray(b"test-key"),
            search_url=SecretStr("https://api.serpbase.dev"),
            extension={"gl": "cn", "hl": "zh-cn"},
        )

        assert wrapper.gl == "cn"
        assert wrapper.hl == "zh-cn"

        wrapper.google_search_results("test query")
        payload = mock_post.call_args[1]["json"]
        assert payload["gl"] == "cn"
        assert payload["hl"] == "zh-cn"

    @pytest.mark.parametrize(
        "status, message, retryable",
        [
            (1001, "invalid api key", False),
            (1020, "no credits", False),
            (1029, "rate limited", True),
            (1501, "upstream timeout", True),
        ],
    )
    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    @patch(f"{MODULE_PATH}.requests.post")
    def test_business_status_error(self, mock_post, mock_ssl_config, status, message, retryable):
        """HTTP 200 但业务 status 非 0 时必须抛错，不能当成功结果返回"""
        mock_ssl_config.return_value = (False, None)
        mock_post.return_value = _mock_response(
            {"status": status, "request_id": "r-test", "message": message}
        )

        with pytest.raises(SerpBaseSearchResponseError) as exc_info:
            self.wrapper.google_search_results("test query")

        error = exc_info.value
        assert error.status == status
        assert error.request_id == "r-test"
        assert error.retryable is retryable
        assert str(status) in str(error)
        # 错误信息不得泄漏密钥
        assert "test-api-key-123" not in str(error)

    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    @patch(f"{MODULE_PATH}.requests.post")
    def test_business_status_error_does_not_log_api_key(self, mock_post, mock_ssl_config, caplog):
        """业务错误日志只记录 status/request_id"""
        mock_ssl_config.return_value = (False, None)
        mock_post.return_value = _mock_response(
            {"status": 1001, "request_id": "r-test", "message": "invalid api key"}
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SerpBaseSearchResponseError):
                self.wrapper.google_search_results("test query")

        assert "test-api-key-123" not in caplog.text
        assert "1001" in caplog.text

    @patch(f"{MODULE_PATH}.requests.post")
    def test_google_search_results_with_exception(self, mock_post):
        """测试搜索时的 HTTP 异常处理"""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("API Error")
        mock_post.return_value = mock_response

        with pytest.raises(requests.exceptions.HTTPError):
            self.wrapper.google_search_results("test query")

    def test_missing_organic_returns_empty_list(self):
        """status=0 但没有 organic 字段时返回空列表"""
        assert self.wrapper._parsed_results({"status": 0}) == []
        assert self.wrapper._parsed_results({"status": "0", "organic": []}) == []

    def test_non_dict_payload_returns_empty_list(self):
        """非 dict 响应视为无结果"""
        assert self.wrapper._parsed_results(["unexpected"]) == []

    @patch(
        f"{MODULE_PATH}.SerpBaseSearchAPIWrapper.google_search_results"
    )
    def test_results_method(self, mock_search_results):
        """测试results包装方法"""
        expected_result = [{"title": "Test"}]
        mock_search_results.return_value = expected_result

        result = self.wrapper.results("test query")

        mock_search_results.assert_called_once_with(search_term="test query")
        assert result == expected_result

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    async def test_async_google_search_results(self, mock_ssl_config):
        """测试异步搜索功能"""
        mock_ssl_config.return_value = (True, "/path/to/cert")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SUCCESS_PAYLOAD

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

        self.wrapper.gl = "cn"
        self.wrapper.hl = "zh-cn"
        with patch(f"{MODULE_PATH}.httpx.AsyncClient", return_value=mock_client):
            result = await self.wrapper.async_google_search_results("async query")

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args

            assert call_args[0][0] == "https://api.serpbase.dev/google/search"
            assert call_args[1]['headers']['X-API-Key'] == "test-api-key-123"
            payload = call_args[1]['json']
            assert payload['q'] == "async query"
            assert payload['gl'] == "cn"
            assert payload['hl'] == "zh-cn"

            assert isinstance(result, list)
            assert result[0]["title"] == "Test Result"

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
    async def test_async_business_status_error(self, mock_ssl_config):
        """异步路径同样校验业务 status"""
        mock_ssl_config.return_value = (False, None)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"status": 1029, "request_id": "r-async"}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

        with patch(f"{MODULE_PATH}.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(SerpBaseSearchResponseError) as exc_info:
                await self.wrapper.async_google_search_results("async query")

        assert exc_info.value.request_id == "r-async"
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    @patch(
        f"{MODULE_PATH}.SerpBaseSearchAPIWrapper.async_google_search_results"
    )
    async def test_aresults_method(self, mock_async_search_results):
        """测试异步包装方法"""
        expected_result = [{"title": "Async Test"}]
        mock_async_search_results.return_value = expected_result

        result = await self.wrapper.aresults("test query")

        mock_async_search_results.assert_called_once_with(search_term="test query")
        assert result == expected_result

    def test_model_config(self):
        """测试extra='allow'允许额外字段"""
        wrapper_with_extra = SerpBaseSearchAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=SecretStr(DEFAULT_SERPBASE_SEARCH_URL),
            extra_field="allowed"
        )
        assert hasattr(wrapper_with_extra, 'extra_field')

    def test_search_url_secret(self):
        """测试SecretStr处理"""
        secret_url = SecretStr(DEFAULT_SERPBASE_SEARCH_URL)
        wrapper = SerpBaseSearchAPIWrapper(
            search_api_key=bytearray(b"key"),
            search_url=secret_url
        )

        assert wrapper.search_url.get_secret_value() == DEFAULT_SERPBASE_SEARCH_URL


def test_serpbase_provider_registration():
    """serpbase 必须完成枚举、配置、导出与 web_search 映射的注册"""
    from openjiuwen_deepsearch.config.config import WebSearchEngineConfig
    from openjiuwen_deepsearch.framework.openjiuwen.tools import search_api
    from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api import serpbase
    from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping
    from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine

    assert SearchEngine.SERPBASE.value == "serpbase"
    assert serpbase.SerpBaseSearchAPIWrapper is SerpBaseSearchAPIWrapper
    assert search_api.SerpBaseSearchAPIWrapper is SerpBaseSearchAPIWrapper
    assert "SerpBaseSearchAPIWrapper" in serpbase.__all__
    assert "SerpBaseSearchAPIWrapper" in search_api.__all__
    assert search_engine_mapping["serpbase"] is SerpBaseSearchAPIWrapper
    assert search_engine_mapping["serper"] is not SerpBaseSearchAPIWrapper

    config = WebSearchEngineConfig.model_validate({"search_engine_name": "serpbase"})
    assert config.search_engine_name == "serpbase"

    with pytest.raises(ValidationError):
        WebSearchEngineConfig.model_validate({"search_engine_name": "serpbase_typo"})


@patch(f"{MODULE_PATH}.requests.post")
@patch(f"{MODULE_PATH}.SslUtils.get_ssl_config")
def test_registered_wrapper_is_callable_through_mapping(mock_ssl_config, mock_post):
    """注册后的 wrapper 可以通过 search_engine_mapping 实例化并返回结果"""
    from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping

    mock_ssl_config.return_value = (False, None)
    mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)

    wrapper_class = search_engine_mapping["serpbase"]
    wrapper = wrapper_class(
        search_api_key=bytearray(b"test-api-key-123"),
        search_url="",
        max_web_search_results=3,
        extension={},
    )

    assert wrapper.results("test query")[0]["link"] == "https://example.com/page"
    assert mock_post.call_args[0][0] == f"{DEFAULT_SERPBASE_SEARCH_URL}/google/search"
