# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""服务端请求与响应模型。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., description="问题描述（Issue、缺陷报告等）")
    collection: str = Field(..., description="已索引的集合名称")
    revision: str = Field(default="local", description="版本标签，需与索引时一致")
    top_k: int = Field(default=20, ge=1, le=100)


class HitModel(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    text: str


class SearchResponse(BaseModel):
    termination: str
    turns: int
    total_input_tokens: int
    total_output_tokens: int
    hits: list[HitModel]


class IndexRequest(BaseModel):
    repo_path: str = Field(..., description="服务端可访问的仓库路径")
    collection: str
    revision: str = "local"
    reset: bool = False


class JobResponse(BaseModel):
    job_id: str
    status: Literal["running", "succeeded", "failed"]
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
