from __future__ import annotations

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.utils import ensure_api_keys_bytearray
from openjiuwen_deepsearch.algorithm.search_tools.web_fetch_tool import WebFetch
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api import (
    JinaWebFetchProvider,
    resolve_web_fetch_provider,
)

pytestmark = pytest.mark.unit


def test_resolve_web_fetch_provider_returns_jina_instance() -> None:
    provider_name, provider = resolve_web_fetch_provider(
        {
            "provider_name": "jina",
            "api_key": bytearray(b"fetch-key"),
        }
    )

    assert provider_name == "jina"
    assert isinstance(provider, JinaWebFetchProvider)


@pytest.mark.asyncio
async def test_web_fetch_returns_controlled_error_without_explicit_provider() -> None:
    fetch = WebFetch({})

    result = await fetch.acall({"url": "https://example.com", "goal": "Find facts"})

    assert result == (
        "[web_fetch] No fetch provider configured. "
        "Set agent_config.web_fetch_provider_config.provider_name explicitly."
    )


@pytest.mark.asyncio
async def test_web_fetch_returns_controlled_error_for_unsupported_provider() -> None:
    fetch = WebFetch({"web_fetch_provider_config": {"provider_name": "unknown"}})

    result = await fetch.acall({"url": "https://example.com", "goal": "Find facts"})

    assert result == "[web_fetch] Unsupported fetch provider 'unknown'. Supported providers: jina."


def test_ensure_api_keys_bytearray_converts_fetch_provider_api_key() -> None:
    config = ensure_api_keys_bytearray(
        {
            "web_fetch_provider_config": {
                "provider_name": "jina",
                "api_key": "fetch-key",
            }
        }
    )

    assert config["web_fetch_provider_config"]["api_key"] == bytearray(b"fetch-key")
