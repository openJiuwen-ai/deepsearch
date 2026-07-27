from pydantic import SecretStr

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.petal.api_wrapper import (
    PetalSearchAPIWrapper,
)


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
