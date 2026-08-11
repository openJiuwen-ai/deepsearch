# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import List, Optional

from pydantic import BaseModel, Field


class TemplateImportRequest(BaseModel):
    """Request for importing a template"""
    space_id: str = Field(..., description="Space ID")
    file_name: str = Field(..., description="File name with extension")
    file_stream: str = Field(..., description="Base64 encoded file content")
    is_template: bool = Field(..., description="Whether it's a template or sample report")
    template_name: str = Field(..., description="Template name")
    template_desc: str = Field(..., description="Template description")
    llm_config: dict = Field(..., description="LLM configuration")


class TemplateUpdateRequest(BaseModel):
    """Request for updating a specific template"""
    space_id: str = Field(..., description="Space ID")
    template_id: int = Field(..., description="Template ID")
    template_content: str = Field(..., description="Base64 encoded template content")
    template_name: str = Field(..., description="Template name")
    template_desc: str = Field(..., description="Template description")


# Response Models
class TemplateBaseResponse(BaseModel):
    """Base response model"""
    code: int = Field(0, description="Error code (0: success, 1: failure)")
    msg: str = Field("success", description="Result message")


class TemplateImportResponse(TemplateBaseResponse):
    """Response for importing a template"""
    template_id: Optional[int] = Field(None, description="Template ID")


class TemplateUpdateResponse(TemplateBaseResponse):
    """Response for updating a specific template"""
    template_id: Optional[int] = Field(None, description="Template ID")


class TemplateDeleteResponse(TemplateBaseResponse):
    """Response for deleting a specific template"""
    pass


class TemplateGetResponse(TemplateBaseResponse):
    """Response for getting a specific template"""
    template_content: str = Field("", description="Base64 encoded template content")


class TemplateListItem(BaseModel):
    """Template list item"""
    template_name: str = Field(..., description="Template name")
    template_desc: str = Field(..., description="Template description")
    template_id: int = Field(..., description="Template ID")
    create_time: str = Field(..., description="Creation time")


class TemplateListResponse(TemplateBaseResponse):
    """Response for listing templates"""
    data: List[TemplateListItem] = Field(..., description="List of templates")
