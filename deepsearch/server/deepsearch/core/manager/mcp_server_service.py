# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_search_service_url
from server.core.manager.model_manager.utils import SecurityUtils
from server.deepsearch.common.exception.exceptions import (
    McpServerExistsException,
    ValidationError,
    McpServerNotFoundException,
)
from server.deepsearch.core.manager.repositories.mcp_server_repository import (
    McpServerRepositoryInter,
    SENSITIVE_HEADER_KEYS,
)
from server.deepsearch.core.models.mcp_server_model import McpServerModel
from server.schemas.mcp_server import (
    McpServerCreateRequestDTO,
    McpServerCreateRes,
    McpServerGetRequestDTO,
    McpServerGetRes,
    McpServerListRequestDTO,
    McpServerListRes,
    McpServerDeleteRequestDTO,
    McpServerDeleteRes,
    McpServerUpdateRequestDTO,
    McpServerUpdateRes,
    McpServerItem,
)

logger = logging.getLogger(__name__)


def _encrypt_sensitive_headers(headers: dict) -> dict:
    """加密敏感 header value"""
    security_utils = SecurityUtils()
    encrypted = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADER_KEYS and isinstance(value, str) and value:
            encrypted[key] = security_utils.encrypt_api_key(value)
        else:
            encrypted[key] = value
    return encrypted


class McpServerService:

    def __init__(self, mcp_server_repository: McpServerRepositoryInter) -> None:
        self.repository = mcp_server_repository

    @staticmethod
    def _validate_server_url(server_url: str | None) -> None:
        """Reject user-configured server URLs that target private or non-public hosts."""
        url = (server_url or "").strip()
        if not url:
            return
        try:
            validate_search_service_url(url)
        except CustomValueException as exc:
            raise ValidationError(f"Invalid server_url: {exc}") from exc

    def create_mcp_server(self, create_request: McpServerCreateRequestDTO):
        try:
            logger.info(
                "Creating mcp server space_id=%s server=%s",
                create_request.space_id,
                create_request.server_name,
            )
            model = self.repository.get_by_name(create_request.space_id, create_request.server_name)
            if model:
                raise McpServerExistsException(
                    f"MCP server {create_request.server_name} already exists under your space."
                )

            self._validate_server_url(create_request.server_url)

            if create_request.headers:
                create_request.headers = _encrypt_sensitive_headers(create_request.headers)

            patch = create_request.model_dump(exclude_unset=True)
            dao_model = McpServerModel()
            for key, value in patch.items():
                setattr(dao_model, key, value)
            self.repository.create(dao_model)
            logger.info(
                "Created mcp server space_id=%s mcp_server_id=%s",
                create_request.space_id,
                dao_model.mcp_server_id,
            )
            return McpServerCreateRes.model_validate(dao_model)
        except McpServerExistsException:
            raise
        except Exception as e:
            logger.error("Failed to create mcp server: %s", str(e))
            raise ValidationError(f"Failed to create mcp server: {str(e)}") from e

    def get_mcp_server_by_id(self, request: McpServerGetRequestDTO):
        try:
            logger.info(
                "Getting mcp server space_id=%s mcp_server_id=%s",
                request.space_id,
                request.mcp_server_id,
            )
            record = self.repository.get_by_id(request.space_id, request.mcp_server_id)
            if not record:
                raise McpServerNotFoundException(
                    f"MCP server id {request.mcp_server_id} not found under your space."
                )
            return McpServerGetRes.model_validate(record)
        except McpServerNotFoundException:
            raise
        except Exception as e:
            logger.error("Failed to get mcp server: %s", str(e))
            raise ValidationError(f"Failed to get mcp server: {str(e)}") from e

    def get_mcp_server_list(self, request: McpServerListRequestDTO):
        try:
            logger.info("Listing mcp servers space_id=%s", request.space_id)
            records = self.repository.get_list_by_id(request.space_id)
            server_list = McpServerListRes()
            items = []
            for item in records:
                server_item = McpServerItem.model_validate(item)
                items.append(server_item)
            server_list.data = items
            logger.info(
                "Listed mcp servers space_id=%s count=%s",
                request.space_id,
                len(items),
            )
            return server_list
        except Exception as e:
            logger.error("Failed to get mcp server list: %s", str(e))
            raise ValidationError(f"Failed to get mcp server list: {str(e)}") from e

    def delete_mcp_server_by_id(self, request: McpServerDeleteRequestDTO):
        try:
            logger.info(
                "Deleting mcp server space_id=%s mcp_server_id=%s",
                request.space_id,
                request.mcp_server_id,
            )
            self.repository.delete_by_id(request.space_id, request.mcp_server_id)
            logger.info(
                "Deleted mcp server space_id=%s mcp_server_id=%s",
                request.space_id,
                request.mcp_server_id,
            )
            return McpServerDeleteRes()
        except McpServerNotFoundException:
            raise
        except Exception as e:
            logger.error("Failed to delete mcp server: %s", str(e))
            raise ValidationError(f"Failed to delete mcp server: {str(e)}") from e

    def update_mcp_server(self, request: McpServerUpdateRequestDTO):
        try:
            logger.info(
                "Updating mcp server space_id=%s mcp_server_id=%s",
                request.space_id,
                request.mcp_server_id,
            )
            existing_record = self.repository.get_by_id(request.space_id, request.mcp_server_id)
            if not existing_record:
                raise McpServerNotFoundException(
                    f"MCP server {request.mcp_server_id} not found under your space."
                )

            if request.server_url is not None:
                self._validate_server_url(request.server_url)

            if request.headers:
                request.headers = _encrypt_sensitive_headers(request.headers)

            update_data = request.model_dump(exclude_unset=True)
            temp_model = McpServerModel()
            for key, value in update_data.items():
                setattr(temp_model, key, value)

            self.repository.update(temp_model)

            updated_record = self.repository.get_by_id(request.space_id, request.mcp_server_id)
            logger.info(
                "Updated mcp server space_id=%s mcp_server_id=%s",
                request.space_id,
                request.mcp_server_id,
            )
            return McpServerUpdateRes.model_validate(updated_record)
        except McpServerNotFoundException:
            raise
        except Exception as e:
            logger.error("Failed to update mcp server: %s", str(e))
            raise ValidationError(f"Failed to update mcp server: {str(e)}") from e
