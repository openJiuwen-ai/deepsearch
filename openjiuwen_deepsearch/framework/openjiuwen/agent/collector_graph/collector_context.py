# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from pydantic import BaseModel, Field

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery


class CollectorContext(BaseModel):
    language: str = Field(default="", description="语言")  # public
    messages: list = Field(default=[], description="消息列表")  # public
    section_idx: int | str = Field(default=0, description="章节索引")  # public
    plan_idx: int | str = Field(default=0, description="计划索引")  # public
    step_idx: int | str = Field(default=0, description="步骤索引")  # public
    step_title: str = Field(default="", description="步骤标题")
    step_description: str = Field(default="", description="步骤描述")
    initial_search_query_count: int = Field(default=1, description="初始查询计数")
    max_research_loops: int = Field(default=2, description="最大循环限制")
    max_react_recursion_limit: int = Field(default=8, description="最大递归限制")
    research_loop_count: int = Field(default=0, description="研究循环计数")
    max_tool_steps: int = Field(default=3, description="最大步数")
    search_queries: list[RetrievalQuery] = Field(default=[], description="当前的检索Query列表")  # info_collector
    history_queries: list[RetrievalQuery] = Field(default=[], description="历史的检索Query列表")  # info_collector
    doc_infos: list = Field(default=[], description="收集的文档信息")
    new_doc_infos_current_loop: list = Field(default=[], description="当前循环新增文件信息，用于流式输出")
    is_sufficient: bool = Field(default=False, description="是否足够")  # Supervisor
    knowledge_gap: str = Field(default="", description="内容差距")
    info_summary: str = Field(default="", description="总结")  # Summary
    need_programmer: bool = Field(default=False, description="是否需要程序")
    programmer_task: str = Field(default="", description="Programmer任务")
    evaluation: str = Field(default="", description="步骤收集信息的结果评估")
