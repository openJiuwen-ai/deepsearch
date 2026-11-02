# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""服务端请求与响应模型。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from openjiuwen_codesearch.config.agent import DEFAULT_ENGINE

# 与 SearchAgentConfig.engine 对齐；默认 graph，不把 retropus 设为默认
EngineName = Literal["react", "graph", "retropus"]


class SearchRequest(BaseModel):
    query: str = Field(..., description="问题描述（Issue、缺陷报告等）")
    collection: str = Field(..., description="已索引的集合名称")
    revision: str = Field(default="local", description="版本标签，需与索引时一致")
    top_k: int = Field(default=20, ge=1, le=100)
    engine: EngineName = Field(
        default=DEFAULT_ENGINE,
        description="检索引擎；retropus 需显式指定，且须与索引时一致",
    )


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
    engine: EngineName = Field(
        default=DEFAULT_ENGINE,
        description="索引引擎；retropus 需显式指定（进程内 KG/BM25，无 Milvus）",
    )


class JobResponse(BaseModel):
    job_id: str
    status: Literal["running", "succeeded", "failed"]
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
