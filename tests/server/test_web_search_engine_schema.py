# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from server.schemas.web_search_engine import (
    WebSearchEngineCreateRequestDTO,
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
