from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from pydantic import SecretStr

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.petal.api_wrapper import (
    PetalSearchAPIWrapper,
)

MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.petal.api_wrapper"


class TestPetalSearchAPIWrapper:
    def test_build_headers_default_content_true(self):
        wrapper = PetalSearchAPIWrapper(
            search_api_key=bytearray(b"test-key"),
            search_url=SecretStr("https://petal.example.com/search"),
        )

        _, _, payload = wrapper.build_headers("test query")
        assert payload["content"] is True

    def test_build_headers_extension_content_false(self):
        wrapper = PetalSearchAPIWrapper(
            search_api_key=bytearray(b"test-key"),
            search_url=SecretStr("https://petal.example.com/search"),
            extension={"content": False},
        )

        _, _, payload = wrapper.build_headers("test query")
        assert payload["content"] is False

    @pytest.mark.asyncio
    async def test_async_search_api_results_trust_env(self):
        """验证 ClientSession 启用 trust_env，读取运行环境代理配置"""
        wrapper = PetalSearchAPIWrapper(
            search_api_key=bytearray(b"test-key"),
            search_url=SecretStr("https://petal.example.com/search"),
        )

        with patch(f"{MODULE_PATH}.aiohttp.ClientSession") as mock_client_session, \
                patch(f"{MODULE_PATH}.SslUtils.get_ssl_config") as mock_ssl_config:
            mock_ssl_config.return_value = (True, "/path/to/cert")

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json.return_value = {"web_pages": []}

            mock_post_context = MagicMock()
            mock_post_context.__aenter__.return_value = mock_response
            mock_post_context.__aexit__.return_value = None

            mock_session = MagicMock()
            mock_session.post.return_value = mock_post_context

            mock_session_context = MagicMock()
            mock_session_context.__aenter__.return_value = mock_session
            mock_session_context.__aexit__.return_value = None

            mock_client_session.return_value = mock_session_context

            await wrapper._async_search_api_results("test query", num=5)

            _, session_kwargs = mock_client_session.call_args
            assert session_kwargs["trust_env"] is True
