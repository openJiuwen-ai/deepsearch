# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json

from fastapi import status

from server.local_retrieval.core.manager import knowledge_base as kb_manager
from server.schemas.common import ResponseModel
from server.schemas.knowledge_base import (
    KnowledgeBaseListRequest,
    KnowledgeBaseSearchRequest,
)


_EMBED_API_KEY = "embed-api-key-must-not-leak"
_LLM_API_KEY = "llm-api-key-must-not-leak"
_EXTRA_SECRET = "custom-secret-must-not-leak"


def _knowledge_base_record() -> dict:
    return {
        "kb_id": "kb-1",
        "space_id": "space-1",
        "name": "Finance knowledge base",
        "description": "Quarterly reports",
        "config": {
            "embed_model_config": {
                "model_name": "embedding-model",
                "api_key": _EMBED_API_KEY,
                "base_url": "https://embedding.example.test",
            },
            "llm_config": {
                "model_name": "llm-model",
                "api_key": _LLM_API_KEY,
                "base_url": "https://llm.example.test",
            },
            "integration_token": _EXTRA_SECRET,
        },
        "create_time": 1_700_000_000_000,
        "update_time": 1_700_000_100_000,
    }


def test_knowledge_base_search_excludes_stored_model_configuration(monkeypatch):
    """Search responses expose KB metadata but never stored credentials."""
    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "knowledge_base_search",
        lambda **_: ResponseModel(
            code=status.HTTP_200_OK,
            message="Success",
            data={
                "knowledge_bases": [_knowledge_base_record()],
                "total": 1,
                "page": 1,
                "page_size": 10,
                "total_pages": 1,
            },
        ),
    )
    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "has_graph_enhancement_documents",
        lambda **_: False,
    )

    response = kb_manager.knowledge_base_search(
        KnowledgeBaseSearchRequest(space_id="space-1", query="Finance")
    )

    assert response.code == status.HTTP_200_OK
    item = response.data["knowledge_bases"][0]
    assert item == {
        "id": "kb-1",
        "space_id": "space-1",
        "name": "Finance knowledge base",
        "description": "Quarterly reports",
        "create_time": 1_700_000_000_000,
        "update_time": 1_700_000_100_000,
        "has_graph_enhancement": False,
    }

    serialized_response = json.dumps(response.data)
    for secret in (_EMBED_API_KEY, _LLM_API_KEY, _EXTRA_SECRET):
        assert secret not in serialized_response


def test_knowledge_base_list_does_not_serialize_stored_configuration(monkeypatch):
    """List responses remain metadata-only when repository records contain secrets."""
    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "knowledge_base_list",
        lambda **_: ResponseModel(
            code=status.HTTP_200_OK,
            message="Success",
            data={"items": [_knowledge_base_record()], "total": 1},
        ),
    )
    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "has_graph_enhancement_documents",
        lambda **_: False,
    )
    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "document_status_list",
        lambda **_: ResponseModel(
            code=status.HTTP_200_OK,
            message="Success",
            data=["indexed"],
        ),
    )

    response = kb_manager.knowledge_base_list(
        KnowledgeBaseListRequest(space_id="space-1", page=1, size=10)
    )

    assert response.code == status.HTTP_200_OK
    item = response.data["items"][0]
    assert item["id"] == "kb-1"
    assert item["name"] == "Finance knowledge base"
    assert item["status"] == "indexed"
    assert "config" not in item

    serialized_response = json.dumps(response.data)
    for secret in (_EMBED_API_KEY, _LLM_API_KEY, _EXTRA_SECRET):
        assert secret not in serialized_response
