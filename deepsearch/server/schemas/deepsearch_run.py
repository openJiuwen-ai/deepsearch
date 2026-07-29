#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import Literal, List

from pydantic import BaseModel, Field, field_validator

from openjiuwen_deepsearch.utils.validation_utils.param_validation import (
    SAFE_CONVERSATION_ID_PATTERN,
)

_CONVERSATION_ID_SCHEMA_ERR = (
    "conversation_id must be 1–128 characters and use only ASCII letters, digits, "
    "underscore, or hyphen (^[A-Za-z0-9_-]{1,128}$)."
)


class WebSearchConfig(BaseModel):
    web_search_config_id: int = Field(description="联网增强引擎ID")
    max_web_search_results: int = Field(default=5, ge=1, le=10, description="一次网页搜索的最大返回结果数量")


class LocalSearchConfig(BaseModel):
    local_search_config_ids: List[str] = Field(default=[], description="本地知识库ID列表")
    max_local_search_results: int = Field(default=5, ge=1, le=10, description="最大本地搜索结果数量")
    recall_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="知识库检索阈值")


class PluginToolParam(BaseModel):
    name: str = Field(..., description="参数名称")
    desc: str = Field(default="", description="参数描述")
    type: int = Field(default=1, description="参数类型")
    is_required: bool = Field(default=False, description="是否必填")
    method: int = Field(default=0, description="参数发送方式")
    is_runtime: bool = Field(default=False, description="是否运行时参数")
    value: str = Field(default="", description="默认值")
    priority: int = Field(default=0, description="参数优先级")


class PluginApiHeader(BaseModel):
    name: str = Field(..., description="请求头名称")
    value: str = Field(default="", description="请求头值")


class RuntimeApiToolRequest(BaseModel):
    tool_id: str = Field(..., description="工具ID")
    name: str = Field(..., description="工具名称")
    desc: str = Field(default="", description="工具描述")
    response_wrapper: str = Field(default="", description="响应包装器类型，当前支持 search_result")
    path: str = Field(..., description="接口路径或完整 URL")
    method: int | str = Field(..., description="HTTP 方法")
    request_params: List[PluginToolParam] = Field(default_factory=list, description="请求参数")
    response_params: List[PluginToolParam] = Field(
        default_factory=list,
        description="响应参数定义，当前仅透传保留，实际返回结构请优先使用 response_wrapper 约束",
    )
    headers: List[PluginApiHeader] = Field(default_factory=list, description="默认请求头")
    plugin_id: str = Field(default="", description="插件ID")
    plugin_version: str | None = Field(default=None, description="插件版本")
    url: str = Field(default="", description="插件服务基址")


class DeepSearchRequest(BaseModel):
    space_id: str = Field(..., description="用户空间ID")
    conversation_id: str = Field(..., description="请求对话ID")
    message: str = Field(..., description="用户请求查询或者人机交互时的反馈")
    workflow_human_in_the_loop: bool = Field(default=True, description="是否启用人机交互")
    outliner_max_section_num: int = Field(default=10, ge=1, le=15, description="最大规划章节数量，取值范围:[1,15]")
    source_tracer_research_trace_source_switch: bool = Field(default=True, description="溯源功能开关")
    source_tracer_generated_citation_switch: bool = Field(default=True, description="新增引用生成开关")
    source_tracer_infer_switch: bool = Field(default=True, description="溯源推理功能开关")
    info_collector_search_method: Literal["web", "local", "all"] = Field(default="web",
                                                                         description="搜索方式："
                                                                                     "web: 联网搜索"
                                                                                     "local: 本地搜索工具搜索"
                                                                                     "all : 联网+本地融合搜索")
    info_collector_webpage_enrich_enable: bool = Field(
        default=False,
        description="是否启用信息收集阶段的网页正文增强",
    )
    llm_config: dict = Field(default_factory=dict, description="LLM配置")
    web_search_config: WebSearchConfig = Field(default=None, description="联网增强引擎配置，和本地知识库配置至少选择一个")
    local_search_config: LocalSearchConfig = Field(default=None,
                                                   description="本地知识库配置，和联网增强引擎配置至少选择一个")
    template_id: int = Field(default=-1, description="报告模板ID（可选）")
    interrupt_feedback: Literal[
        "", "accepted", "cancel", "revise_outline", "revise_comment"
    ] = Field(default="", description="中断反馈标识（可选）")
    outline_interaction_enabled: bool = Field(default=False, description="大纲交互开关")
    outline_interaction_max_rounds: int = Field(default=3, ge=1, le=100, description="大纲交互最大轮次")
    search_mode: Literal["research", "search", "react"] = Field(
        default="research",
        description="research: 报告; search: DeepSearch; react: 简单 ReAct",
    )
    enable_question_router: bool = Field(
        default=False,
        description="search 模式下先经 LLM 路由：0→react，1→search（DeepSearch）",
    )
    execution_method: Literal["parallel", "dependency_driving", "hybrid"] = Field(
        default="parallel",
        description=(
            "执行方法："
            "parallel：并行工作流执行；"
            "dependency_driving：依赖驱动工作流执行；"
            "hybrid：混合大纲路由模式，由意图识别节点调用LLM选择普通大纲或依赖驱动大纲。"
        ),
    )
    web_search_max_qps: float = Field(default=0, description="联网增强引擎最大 QPS，0 表示不限流，支持浮点数如 0.5 表示每 2 秒 1 个请求")
    user_feedback_processor_enable: bool = Field(default=False, description="是否启用用户反馈优化功能")
    user_feedback_processor_max_interactions: int = Field(default=100, ge=1, le=100, description="最大交互次数")
    stats_info_llm: bool = Field(default=False, description="是否开启 LLM 调用统计")
    tools: List[RuntimeApiToolRequest] = Field(default_factory=list, description="前端传入的 API 工具列表")
    vlm_chart_generator_enable: bool = Field(default=False, description="vlm迭代生成图开关")
    vlm_chart_generator_max_iterations: int = Field(default=1, ge=1, le=3, description="vlm迭代生成图最大迭代次数")
    agent_llm_timeouts: dict[str, int] = Field(default_factory=dict, description="按 agent 配置的 LLM 总超时时间")

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id_chars(cls, v: str) -> str:
        if not SAFE_CONVERSATION_ID_PATTERN.fullmatch(v):
            raise ValueError(_CONVERSATION_ID_SCHEMA_ERR)
        return v
