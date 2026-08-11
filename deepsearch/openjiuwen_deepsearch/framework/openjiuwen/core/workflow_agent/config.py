# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.controller.config import ControllerConfig


class DefaultResponse(BaseModel):
    """Default response when workflow selection fails."""

    type: str = "text"
    text: Optional[str] = None


class WorkflowControllerConfig(ControllerConfig):
    """Config compatible with new ControllerAgent and this WorkflowController."""

    id: str = Field(default="")
    version: str = Field(default="1.0")
    description: str = Field(default="")
    workflows: List[Any] = Field(default_factory=list)
    default_response: DefaultResponse = Field(
        default_factory=DefaultResponse,
    )
