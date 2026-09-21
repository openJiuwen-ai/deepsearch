# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import List

from pydantic import BaseModel, Field, ConfigDict


class McpServerBasicRequestDTO(BaseModel):
    '''MCP server 基类对象'''
    space_id: str = Field(..., min_length=1, max_length=255, description="用户空间id")


class McpServerCreateRequestDTO(McpServerBasicRequestDTO):
    '''MCP server 创建请求对象'''
    model_config = ConfigDict(from_attributes=True)
    server_name: str = Field(..., min_length=1, max_length=255, description="MCP server 名称")
    server_url: str = Field(..., min_length=1, max_length=1024, description="MCP server URL")
    transport_type: str = Field(default="streamable_http", max_length=32, description="传输类型: sse/streamable_http")
    headers: dict | None = Field(default=None, description="请求头字典，敏感 header value 加密存储")
    timeout: float = Field(default=30.0, description="连接/读取超时（秒）")
    type: str = Field(default="search", max_length=64, description="MCP server 用途类型")
    extension: dict | None = Field(default=None, description="扩展配置")
    is_active: bool | None = Field(default=None, description="是否激活")


class McpServerGetRequestDTO(McpServerBasicRequestDTO):
    '''获取指定 MCP server 请求对象'''
    mcp_server_id: int = Field(..., description="MCP server id")


class McpServerListRequestDTO(McpServerBasicRequestDTO):
    '''获取 MCP server 列表请求对象'''
    pass


class McpServerDeleteRequestDTO(McpServerBasicRequestDTO):
    '''删除指定 MCP server 请求对象'''
    mcp_server_id: int = Field(..., description="MCP server id")


class McpServerUpdateRequestDTO(McpServerBasicRequestDTO):
    '''更新指定 MCP server 对象'''
    model_config = ConfigDict(from_attributes=True)
    mcp_server_id: int = Field(..., description="MCP server id")
    server_name: str | None = Field(default=None, min_length=1, max_length=255, description="MCP server 名称")
    server_url: str | None = Field(default=None, min_length=1, max_length=1024, description="MCP server URL")
    transport_type: str | None = Field(default=None, max_length=32, description="传输类型")
    headers: dict | None = Field(default=None, description="请求头字典")
    timeout: float | None = Field(default=None, description="超时（秒）")
    type: str | None = Field(default=None, max_length=64, description="用途类型")
    extension: dict | None = Field(default=None, description="扩展配置")
    is_active: bool | None = Field(default=None, description="是否激活")


class BasicResponseDTO(BaseModel):
    '''MCP server 返回对象基类'''
    model_config = ConfigDict(from_attributes=True)
    code: int = Field(default=200, description="是否成功")
    msg: str = Field(default='success', min_length=1, max_length=255, description="结果信息")


class McpServerCreateRes(BasicResponseDTO):
    '''创建 MCP server 返回对象'''
    mcp_server_id: int = Field(..., description="MCP server id")


class McpServerGetRes(BasicResponseDTO):
    '''获取指定 MCP server'''
    server_name: str = Field(..., min_length=1, max_length=255, description="MCP server 名称")
    server_url: str = Field(..., max_length=1024, description="MCP server URL")
    transport_type: str = Field(default="streamable_http", description="传输类型")
    timeout: float = Field(default=30.0, description="超时")
    type: str = Field(default="search", description="用途类型")
    extension: dict | None = Field(default=None, description="扩展配置")
    is_active: bool | None = Field(default=None, description="是否激活")


class McpServerDetail(BasicResponseDTO):
    '''获取指定 MCP server 详细信息（含解密后 headers）'''
    server_name: str = Field(..., min_length=1, max_length=255, description="MCP server 名称")
    server_url: str = Field(..., max_length=1024, description="MCP server URL")
    transport_type: str = Field(default="streamable_http", description="传输类型")
    headers: dict = Field(default_factory=dict, description="解密后的请求头")
    timeout: float = Field(default=30.0, description="超时")
    type: str = Field(default="search", description="用途类型")
    extension: dict | None = Field(default=None, description="扩展配置")
    is_active: bool | None = Field(default=None, description="是否激活")


class McpServerItem(BaseModel):
    '''MCP server 条目'''
    model_config = ConfigDict(from_attributes=True)
    server_name: str = Field(..., min_length=1, max_length=255, description="MCP server 名称")
    server_url: str = Field(..., max_length=1024, description="MCP server URL")
    transport_type: str = Field(default="streamable_http", description="传输类型")
    timeout: float = Field(default=30.0, description="超时")
    mcp_server_id: int = Field(..., description="MCP server id")
    create_time: str = Field(..., min_length=1, max_length=255, description="创建时间")
    type: str = Field(default="search", description="用途类型")
    extension: dict | None = Field(default=None, description="扩展配置")
    is_active: bool | None = Field(default=None, description="是否激活")


class McpServerListRes(BasicResponseDTO):
    '''获取 MCP server 列表'''
    data: List[McpServerItem] = Field(default=[], description="MCP server 列表")


class McpServerDeleteRes(BasicResponseDTO):
    '''删除指定 MCP server 返回对象'''
    pass


class McpServerUpdateRes(BasicResponseDTO):
    '''修改指定 MCP server'''
    mcp_server_id: int = Field(default=0, description="MCP server id")
