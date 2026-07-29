# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Runtime API tool models; lives here so config does not import the tools package."""
from typing import List, Literal

from pydantic import BaseModel, Field


class RuntimeApiToolParamConfig(BaseModel):
    name: str = Field(default="", description="Runtime API tool param name")
    description: str = Field(default="", description="Runtime API tool param description")
    param_type: int = Field(default=1, description="Runtime API tool param type")
    required: bool = Field(default=False, description="Whether the param is required")
    send_method: Literal["none", "header", "query", "body"] = Field(
        default="none",
        description="How the param is sent",
    )
    is_runtime: bool = Field(default=False, description="Whether the param is provided at runtime")
    default_value: str = Field(default="", description="Runtime API tool param default value")
    priority: int = Field(default=0, description="Runtime API tool param priority")


class RuntimeApiHeaderConfig(BaseModel):
    name: str = Field(default="", description="Runtime API tool header name")
    value: str = Field(default="", description="Runtime API tool header value")


class RuntimeApiToolConfig(BaseModel):
    tool_id: str = Field(default="", description="Runtime API tool id")
    name: str = Field(default="", description="Runtime API tool name")
    description: str = Field(default="", description="Runtime API tool description")
    response_wrapper: str = Field(default="", description="Runtime API response wrapper type")
    path: str = Field(default="", description="Runtime API tool path or full URL")
    http_method: Literal["get", "post", "put", "delete", "patch", "head", "options"] = Field(
        default="post",
        description="Runtime API tool HTTP method",
    )
    base_url: str = Field(default="", description="Runtime API tool base URL")
    plugin_id: str = Field(default="", description="Runtime API tool plugin id")
    plugin_version: str = Field(default="", description="Runtime API tool plugin version")
    request_params: List[RuntimeApiToolParamConfig] = Field(
        default_factory=list,
        description="Runtime API tool request params",
    )
    response_params: List[RuntimeApiToolParamConfig] = Field(
        default_factory=list,
        description="Runtime API tool response params reserved for future mapping",
    )
    headers: List[RuntimeApiHeaderConfig] = Field(
        default_factory=list,
        description="Runtime API tool static headers",
    )


class ApiToolsConfig(BaseModel):
    query_understanding_tools: List[RuntimeApiToolConfig] = Field(
        default_factory=list,
        description="Runtime API tools for entry/planner/outliner",
    )
    collector_tools: List[RuntimeApiToolConfig] = Field(
        default_factory=list,
        description="Runtime API tools for collector",
    )
