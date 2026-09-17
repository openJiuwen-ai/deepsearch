# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
from types import SimpleNamespace

import pytest

from server.deepsearch.common.exception.exceptions import ValidationError
from server.schemas.web_search_engine import (
    WebSearchEngineCreateRequestDTO,
    WebSearchEngineListRequestDTO,
    WebSearchEngineUpdateRequestDTO,
)


def test_web_search_engine_create_allows_empty_search_url():
    """Public engines may rely on built-in default URLs."""
    request = WebSearchEngineCreateRequestDTO(
        space_id="space-a",
        search_engine_name="serper",
        search_api_key="secret",
        search_url="",
    )

    assert request.search_url == ""


def test_web_search_engine_update_allows_empty_search_url():
    """Updating a public engine should allow clearing URL to use defaults."""
    request = WebSearchEngineUpdateRequestDTO(
        space_id="space-a",
        web_search_engine_id=1,
        search_url="",
    )

    assert request.search_url == ""


def test_web_search_engine_create_service_defaults_omitted_search_url():
    """Create service should persist empty search_url when the request omits it."""
    from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService

    class FakeRepository:
        def __init__(self):
            self.created = None

        def get_by_name(self, space_id, search_engine_name):
            return None

        def create(self, model):
            model.web_search_engine_id = 1
            self.created = model

    repository = FakeRepository()
    service = WebSearchEngineService(repository)
    request = WebSearchEngineCreateRequestDTO(
        space_id="space",
        search_engine_name="serper",
        search_api_key="key",
    )

    response = service.create_web_search_engine(request)

    assert response.web_search_engine_id == 1
    assert repository.created.search_url == ""


def test_web_search_engine_list_logs_space_and_count(caplog):
    """List service should log space id and result count.

    Args:
        caplog: pytest 日志捕获工具。

    Returns:
        None.
    """
    from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService

    class FakeRepository:
        def get_list_by_id(self, space_id):
            return [
                SimpleNamespace(
                    space_id=space_id,
                    web_search_engine_id=1,
                    search_engine_name="serper",
                    search_url="",
                    create_time="2026-06-05 12:00:00",
                    extension=None,
                    is_active=True,
                )
            ]

    service = WebSearchEngineService(FakeRepository())
    caplog.set_level(logging.INFO, logger="server.deepsearch.core.manager.web_search_engine_service")

    response = service.get_web_search_engine_list(WebSearchEngineListRequestDTO(space_id="space-a"))

    assert len(response.data) == 1
    assert any(
        "Listed web search engines space_id=space-a count=1" in record.message
        for record in caplog.records
    )


def test_create_web_search_engine_rejects_ssrf_url(monkeypatch):
    """Create service must reject search_url pointing to private/non-public hosts."""
    from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService

    class FakeRepository:
        def get_by_name(self, space_id, search_engine_name):
            return None

        def create(self, model):
            model.web_search_engine_id = 1

    monkeypatch.delenv("SEARCH_SERVICE_ALLOW_UNSAFE_URL", raising=False)
    service = WebSearchEngineService(FakeRepository())
    request = WebSearchEngineCreateRequestDTO(
        space_id="space",
        search_engine_name="jina",
        search_api_key="key",
        search_url="http://169.254.169.254/",
    )
    with pytest.raises(ValidationError):
        service.create_web_search_engine(request)


def test_update_web_search_engine_rejects_ssrf_url(monkeypatch):
    """Update service must reject search_url pointing to private/non-public hosts."""
    from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService

    class FakeRepository:
        def get_by_id(self, space_id, web_search_engine_id):
            return SimpleNamespace(search_engine_name="jina")

        def update(self, model):
            pass

    monkeypatch.delenv("SEARCH_SERVICE_ALLOW_UNSAFE_URL", raising=False)
    service = WebSearchEngineService(FakeRepository())
    request = WebSearchEngineUpdateRequestDTO(
        space_id="space",
        web_search_engine_id=1,
        search_url="http://127.0.0.1/",
    )
    with pytest.raises(ValidationError):
        service.update_web_search_engine(request)


def test_update_web_search_engine_allows_clearing_search_url():
    """Updating search_url to empty must be allowed (falls back to provider default)."""
    from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService

    class FakeRepository:
        def get_by_id(self, space_id, web_search_engine_id):
            return SimpleNamespace(
                search_engine_name="jina",
                search_url="",
                extension=None,
                is_active=True,
                web_search_engine_id=web_search_engine_id,
            )

        def update(self, model):
            pass

    service = WebSearchEngineService(FakeRepository())
    request = WebSearchEngineUpdateRequestDTO(
        space_id="space",
        web_search_engine_id=1,
        search_url="",
    )
    response = service.update_web_search_engine(request)
    assert response.web_search_engine_id == 1


def test_run_web_search_engine_rejects_ssrf_url(monkeypatch):
    """Run path must reject a stored search_url targeting private/non-public hosts."""
    from server.schemas.web_search_engine import WebSearchEngineDetail, WebSearchEnginePostRequestDTO
    from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService

    monkeypatch.delenv("SEARCH_SERVICE_ALLOW_UNSAFE_URL", raising=False)
    config = WebSearchEngineDetail(
        search_engine_name="jina",
        search_url="http://169.254.169.254/",
        search_api_key="key",
    )
    request = WebSearchEnginePostRequestDTO(space_id="space", web_search_engine_id=1, query="q")
    with pytest.raises(ValidationError):
        WebSearchEngineService.run_web_search_engine(request, config)
