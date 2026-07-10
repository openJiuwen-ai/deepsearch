from __future__ import annotations

from typing import Protocol


class BaseWebFetchProvider(Protocol):
    """Provider-specific page retrieval / extraction contract for DeepSearch web_fetch."""

    provider_name: str

    def fetch_page(self, url: str) -> str:
        """Fetch and extract raw page text for a single URL."""
        ...
