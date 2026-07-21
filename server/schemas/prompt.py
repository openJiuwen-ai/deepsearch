# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import List, Optional

from pydantic import BaseModel, Field


class PromptImportRequest(BaseModel):
    """导入Prompt模板请求"""
    space_id: str = Field(..., description="Space ID")
    prompt_name: str = Field(..., description="Prompt name")
    prompt_type: str = Field("system", description="Prompt type (system/user/assistant)")
    prompt_content: str = Field(..., description="Prompt content")
    default_prompt: Optional[str] = Field(None, description="Default system prompt content (optional, auto-loaded from file if not provided)")
    description: str = Field("", description="Prompt description")
    is_active: bool = Field(True, description="Whether the prompt is active")


class PromptUpdateRequest(BaseModel):
    """更新Prompt模板请求"""
    space_id: str = Field(..., description="Space ID")
    prompt_id: int = Field(..., description="Prompt ID")
    prompt_content: str = Field(..., description="Prompt content")
    prompt_name: str = Field(..., description="Prompt name")
    prompt_type: str = Field("system", description="Prompt type (system/user/assistant)")
    description: str = Field("", description="Prompt description")
    is_active: bool = Field(True, description="Whether the prompt is active")


class PromptResetRequest(BaseModel):
    """重置Prompt为默认值请求"""
    space_id: str = Field(..., description="Space ID")
    prompt_id: int = Field(..., description="Prompt ID")


# Response Models
class PromptBaseResponse(BaseModel):
    """基础响应模型"""
    code: int = Field(default=0, description="Error code (0: success, 1: failure)")
    msg: str = Field(default="success", description="Result message")


class PromptImportResponse(PromptBaseResponse):
    """导入Prompt响应"""
    prompt_id: Optional[int] = Field(None, description="Prompt ID")


class PromptUpdateResponse(PromptBaseResponse):
    """更新Prompt响应"""
    prompt_id: Optional[int] = Field(None, description="Prompt ID")


class PromptDeleteResponse(PromptBaseResponse):
    """删除Prompt响应"""
    pass


class PromptResetResponse(PromptBaseResponse):
    """重置Prompt响应"""
    prompt_id: Optional[int] = Field(None, description="Prompt ID")


class PromptGetResponse(PromptBaseResponse):
    """获取Prompt详情响应"""
    prompt_id: int = Field(..., description="Prompt ID")
    space_id: str = Field(..., description="Space ID")
    prompt_name: str = Field(..., description="Prompt name")
    prompt_type: str = Field(..., description="Prompt type")
    prompt_content: str = Field(..., description="Prompt content")
    default_prompt: str = Field(..., description="Default system prompt content")
    description: str = Field("", description="Prompt description")
    is_active: bool = Field(..., description="Whether the prompt is active")
    create_time: str = Field(..., description="Creation time")
    update_time: str = Field(..., description="Update time")


class PromptListItem(BaseModel):
    """Prompt列表项"""
    prompt_id: int = Field(..., description="Prompt ID")
    prompt_name: str = Field(..., description="Prompt name")
    prompt_type: str = Field(..., description="Prompt type")
    description: str = Field("", description="Prompt description")
    is_active: bool = Field(..., description="Whether the prompt is active")
    create_time: str = Field(..., description="Creation time")
    update_time: str = Field(..., description="Update time")


class PromptListResponse(PromptBaseResponse):
    """Prompt列表响应"""
    data: List[PromptListItem] = Field(..., description="List of prompts")
