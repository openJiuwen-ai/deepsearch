# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from functools import wraps

from fastapi import APIRouter, status, Depends, HTTPException
from sqlalchemy.orm import Session

from server.core.database import get_db
from server.deepsearch.common.exception.exceptions import (
    McpServerBasicException,
    ValidationError,
)
from server.deepsearch.core.manager.mcp_server_service import McpServerService
from server.deepsearch.core.manager.repositories.mcp_server_repository import McpServerRepository
from server.schemas.mcp_server import (
    McpServerCreateRes,
    McpServerCreateRequestDTO,
    McpServerGetRes,
    McpServerGetRequestDTO,
    McpServerListRes,
    McpServerListRequestDTO,
    McpServerUpdateRes,
    McpServerUpdateRequestDTO,
    McpServerDeleteRes,
    McpServerDeleteRequestDTO,
)

router = APIRouter()


def get_mcp_server_service(db: Session = Depends(get_db)) -> McpServerService:
    mcp_server_repository = McpServerRepository(db)
    return McpServerService(mcp_server_repository)


def handler_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            data = func(*args, **kwargs)
            data.code = 200
            data.msg = "success"
            return data
        except Exception as e:
            if McpServerBasicException.CODE in str(e) or isinstance(e, ValidationError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e

    return wrapper


@router.post("/", response_model=McpServerCreateRes, status_code=status.HTTP_201_CREATED)
@handler_response
def create_mcp_server(
    request: McpServerCreateRequestDTO,
    service: McpServerService = Depends(get_mcp_server_service),
):
    """创建 MCP server 配置。"""
    return service.create_mcp_server(request)


@router.get("/{space_id}/{mcp_server_id}", response_model=McpServerGetRes, status_code=status.HTTP_200_OK)
@handler_response
def get_mcp_server(
    space_id: str,
    mcp_server_id: int,
    service: McpServerService = Depends(get_mcp_server_service),
):
    """按 id 查询 MCP server 配置。"""
    request = McpServerGetRequestDTO(space_id=space_id, mcp_server_id=mcp_server_id)
    return service.get_mcp_server_by_id(request)


@router.get("/{space_id}", response_model=McpServerListRes, status_code=status.HTTP_200_OK)
@handler_response
def get_mcp_server_list(
    space_id: str,
    service: McpServerService = Depends(get_mcp_server_service),
):
    """查询指定空间下的 MCP server 列表。"""
    request = McpServerListRequestDTO(space_id=space_id)
    return service.get_mcp_server_list(request=request)


@router.put("/", response_model=McpServerUpdateRes, status_code=status.HTTP_200_OK)
@handler_response
def update_mcp_server(
    request: McpServerUpdateRequestDTO,
    service: McpServerService = Depends(get_mcp_server_service),
):
    """更新 MCP server 配置。"""
    return service.update_mcp_server(request)


@router.delete("/{space_id}/{mcp_server_id}", response_model=McpServerDeleteRes, status_code=status.HTTP_200_OK)
@handler_response
def delete_mcp_server(
    space_id: str,
    mcp_server_id: int,
    service: McpServerService = Depends(get_mcp_server_service),
):
    """删除指定的 MCP server 配置。"""
    request = McpServerDeleteRequestDTO(space_id=space_id, mcp_server_id=mcp_server_id)
    return service.delete_mcp_server_by_id(request)