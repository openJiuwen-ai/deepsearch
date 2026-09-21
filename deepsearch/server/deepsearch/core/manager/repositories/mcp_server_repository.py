# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from sqlalchemy.orm import Session

from server.core.manager.model_manager.utils import SecurityUtils
from server.deepsearch.common.exception.exceptions import (
    McpServerNotFoundException,
    McpServerHeaderDecryptError,
    ValidationError,
)
from server.deepsearch.core.models.mcp_server_model import McpServerModel
from server.schemas.mcp_server import McpServerDetail


class McpServerRepositoryInter(ABC):

    @abstractmethod
    def create(self, model: McpServerModel) -> int:
        pass

    @abstractmethod
    def get_by_id(self, space_id: str, mcp_server_id: int) -> type[McpServerModel]:
        pass

    @abstractmethod
    def get_by_name(self, space_id: str, server_name: str) -> type[McpServerModel]:
        pass

    @abstractmethod
    def get_list_by_id(self, space_id: str) -> List[type[McpServerModel]]:
        pass

    @abstractmethod
    def delete_by_id(self, space_id: str, mcp_server_id: int) -> None:
        pass

    @abstractmethod
    def update(self, model: McpServerModel):
        pass

    @abstractmethod
    def get_server_detail_by_id(self, space_id: str, mcp_server_id: int) -> Optional[McpServerDetail]:
        pass


logger = logging.getLogger(__name__)

SENSITIVE_HEADER_KEYS = {"authorization", "x-api-key", "x-auth-token"}


class McpServerRepository(McpServerRepositoryInter):

    def __init__(self, db: Session):
        self.db = db

    def delete_by_id(self, space_id: str, mcp_server_id: int):
        record = self.get_by_id(space_id, mcp_server_id)
        if not record:
            raise McpServerNotFoundException(f"mcp server id {mcp_server_id} not found under your space.")
        self.db.delete(record)
        self.db.commit()

    def get_list_by_id(self, space_id: str):
        return self.db.query(McpServerModel).filter(McpServerModel.space_id == space_id).all()

    def get_by_id(self, space_id: str, mcp_server_id: int):
        return self.db.query(McpServerModel).filter(
            McpServerModel.mcp_server_id == mcp_server_id,
            McpServerModel.space_id == space_id,
        ).first()

    def get_by_name(self, space_id: str, server_name: str):
        return self.db.query(McpServerModel).filter(
            McpServerModel.server_name == server_name,
            McpServerModel.space_id == space_id,
        ).first()

    def create(self, model: McpServerModel):
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)

    def update(self, model: McpServerModel):
        record = self.get_by_id(model.space_id, model.mcp_server_id)
        if not record:
            raise McpServerNotFoundException(f"mcp server id {model.mcp_server_id} not found under your space.")
        if model.server_name is not None:
            record.server_name = model.server_name
        if model.server_url is not None:
            record.server_url = model.server_url
        if model.transport_type is not None:
            record.transport_type = model.transport_type
        if model.headers is not None:
            record.headers = model.headers
        if model.timeout is not None:
            record.timeout = model.timeout
        if model.type is not None:
            record.type = model.type
        if model.extension is not None:
            record.extension = model.extension
        if model.is_active is not None:
            record.is_active = model.is_active
        self.db.commit()

    def get_server_detail_by_id(self, space_id: str, mcp_server_id: int) -> Optional[McpServerDetail]:
        try:
            record = self.get_by_id(space_id, mcp_server_id)
            if not record:
                logger.warning("MCP server not found under your space.")
                return None
            result = McpServerDetail.model_validate(record)
            if result.headers:
                try:
                    result.headers = _decrypt_sensitive_headers(result.headers)
                except Exception as e:
                    raise McpServerHeaderDecryptError(f"Header decryption failed: {str(e)}") from e
            return result
        except McpServerHeaderDecryptError:
            raise
        except Exception as e:
            logger.error("Failed to get mcp server: %s", str(e))
            raise ValidationError(f"Failed to get mcp server: {str(e)}") from e


def _decrypt_sensitive_headers(headers: dict) -> dict:
    """解密敏感 header value"""
    security_utils = SecurityUtils()
    decrypted = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADER_KEYS and isinstance(value, str) and value:
            try:
                decrypted[key] = security_utils.decrypt_api_key(value)
            except Exception:
                decrypted[key] = value
        else:
            decrypted[key] = value
    return decrypted