from __future__ import annotations

from typing import Protocol


class BaseWebFetchProvider(Protocol):
    """Provider-specific page retrieval / extraction contract for DeepSearch web_fetch."""

    provider_name: str

    def fetch_page(self, url: str, *, budget: float | None = None) -> str:
        """Fetch and extract raw page text for a single URL.

        Args:
            url: Target page address.
            budget: Optional time budget in seconds for this call. When given, the
                implementation must keep its own retries, backoff and per-request
                timeouts within the budget, so a worker thread ends near the
                deadline instead of running its full fixed schedule. ``None``
                means unbounded.
        """
        ...
