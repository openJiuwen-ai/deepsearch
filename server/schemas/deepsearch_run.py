#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import Literal, List

from pydantic import BaseModel, Field


class WebSearchConfig(BaseModel):
    web_search_config_id: int = Field(description="联网增强引擎ID")
    max_web_search_results: int = Field(default=5, ge=1, le=10, description="一次网页搜索的最大返回结果数量")


class LocalSearchConfig(BaseModel):
    local_search_config_ids: List[str] = Field(default=[], description="本地知识库ID列表")
    max_local_search_results: int = Field(default=5, ge=1, le=10, description="最大本地搜索结果数量")
    recall_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="知识库检索阈值")


class DeepSearchRequest(BaseModel):
    space_id: str = Field(..., description="用户空间ID")
    conversation_id: str = Field(..., description="请求对话ID")
    message: str = Field(..., description="用户请求查询或者人机交互时的反馈")
    workflow_human_in_the_loop: bool = Field(default=True, description="是否启用人机交互")
    outliner_max_section_num: int = Field(default=5, ge=1, le=10, description="最大规划章节数量，取值范围:[1,10]")
    source_tracer_research_trace_source_switch: bool = Field(default=True, description="溯源功能开关")
    source_tracer_infer_switch: bool = Field(default=True, description="溯源推理功能开关")
    info_collector_search_method: Literal["web", "local", "all"] = Field(default="web",
                                                                         description="搜索方式："
                                                                                     "web: 联网搜索"
                                                                                     "local: 本地搜索工具搜索"
                                                                                     "all : 联网+本地融合搜索")
    llm_config: dict = Field(default_factory=dict, description="LLM配置")
    web_search_config: WebSearchConfig = Field(default=None, description="联网增强引擎配置，和本地知识库配置至少选择一个")
    local_search_config: LocalSearchConfig = Field(default=None,
                                                   description="本地知识库配置，和联网增强引擎配置至少选择一个")
    template_id: int = Field(default=-1, description="报告模板ID（可选）")
    interrupt_feedback: Literal[
        "", "accepted", "cancel", "revise_outline", "revise_comment"
    ] = Field(default="", description="中断反馈标识（可选）")
    outline_interaction_enabled: bool = Field(default=True, description="大纲交互开关")
    outline_interaction_max_rounds: int = Field(default=3, ge=1, le=100, description="大纲交互最大轮次")
    search_mode: Literal["research", "search"] = Field(default="research", description="生成研究报告还是生成答案")
    execution_method: Literal["parallel", "dependency_driving"] = Field(default="parallel",
                                                                         description="执行方法："
                                                                                     "parallel: 并行工作流执行"
                                                                                     "dependency_driving: 依赖驱动工作流执行")
    web_search_max_qps: float = Field(default=0, description="联网增强引擎最大 QPS，0 表示不限流，支持浮点数如 0.5 表示每 2 秒 1 个请求")
