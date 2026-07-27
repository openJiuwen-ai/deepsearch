from __future__ import annotations

from typing import Any

from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.base import BaseWebFetchProvider
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api.jina.api_wrapper import (
    JinaWebFetchProvider,
)

fetch_provider_mapping: dict[str, type[BaseWebFetchProvider]] = {
    "jina": JinaWebFetchProvider,
}


def _config_get(config: Any, field_name: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(field_name, default)
    return getattr(config, field_name, default)


def normalize_fetch_provider_name(provider_name: str | None) -> str:
    return str(provider_name or "").strip().lower()


def supported_fetch_providers() -> tuple[str, ...]:
    return tuple(fetch_provider_mapping.keys())


def resolve_web_fetch_provider(config: Any) -> tuple[str, BaseWebFetchProvider | None]:
    provider_name = normalize_fetch_provider_name(_config_get(config, "provider_name", ""))
    if not provider_name:
        return "", None

    provider_cls = fetch_provider_mapping.get(provider_name)
    if provider_cls is None:
        return provider_name, None

    return (
        provider_name,
        provider_cls(
            api_key=_config_get(config, "api_key"),
            base_url=str(_config_get(config, "base_url", "") or ""),
            extension=_config_get(config, "extension", {}) or {},
        ),
    )
