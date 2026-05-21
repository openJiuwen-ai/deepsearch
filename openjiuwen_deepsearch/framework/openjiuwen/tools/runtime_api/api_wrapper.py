#!/usr/bin/env python
# -*- coding: UTF-8 -*-

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any

from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH, MAX_URL_LENGTH

logger = logging.getLogger(__name__)

MAX_RUNTIME_API_SEARCH_RESULTS = 20


class BaseApiWrapper(ABC):
    """Base class for runtime API response wrappers."""

    @abstractmethod
    def wrap(self, payload: Any) -> Any:
        """Wrap runtime API payload into a collector-friendly structure."""


class SearchResultApiWrapper(BaseApiWrapper):
    """Normalize common API payloads into collector search result format."""

    default_search_engine = "runtime_api"

    def wrap(self, payload: Any) -> Any:
        if isinstance(payload, dict) and isinstance(payload.get("search_results"), list):
            return {
                "search_engine": payload.get("search_engine") or self.default_search_engine,
                "search_results": self._normalize_items(payload.get("search_results", [])),
            }

        candidates = self._extract_candidates(payload)
        if candidates is None:
            return payload

        return {
            "search_engine": self.default_search_engine,
            "search_results": self._normalize_items(candidates),
        }

    @staticmethod
    def _extract_candidates(payload: Any) -> list[dict[str, Any]] | None:
        if isinstance(payload, list):
            candidates = payload[:MAX_RUNTIME_API_SEARCH_RESULTS]
            return candidates if all(isinstance(item, dict) for item in candidates) else None

        if not isinstance(payload, dict):
            return None

        for key in ("results", "items", "records", "documents", "docs", "list"):
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            candidates = value[:MAX_RUNTIME_API_SEARCH_RESULTS]
            if all(isinstance(item, dict) for item in candidates):
                return candidates

        if any(
            key in payload
            for key in ("title", "url", "link", "content", "snippet", "summary", "description", "text")
        ):
            return [payload]

        return None

    @staticmethod
    def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_items: list[dict[str, Any]] = []
        for item in items[:MAX_RUNTIME_API_SEARCH_RESULTS]:
            title = str(
                item.get("title")
                or item.get("name")
                or item.get("document_name")
                or item.get("file_name")
                or ""
            )[:MAX_SEARCH_CONTENT_LENGTH]
            url = str(
                item.get("url")
                or item.get("link")
                or item.get("source_url")
                or item.get("source")
                or ""
            )[:MAX_URL_LENGTH]
            content = str(
                item.get("content")
                or item.get("snippet")
                or item.get("summary")
                or item.get("description")
                or item.get("text")
                or item
            )[:MAX_SEARCH_CONTENT_LENGTH]

            normalized_item = {
                "title": title,
                "url": url,
                "content": content,
            }
            if "score" in item:
                normalized_item["score"] = item.get("score")
            normalized_items.append(normalized_item)
        return normalized_items


def build_api_wrapper(wrapper_name: str | None) -> BaseApiWrapper | None:
    if not wrapper_name:
        return None
    if wrapper_name == "search_result":
        return SearchResultApiWrapper()
    logger.warning("[build_api_wrapper] Unknown response wrapper '%s', fallback to raw payload.", wrapper_name)
    return None
