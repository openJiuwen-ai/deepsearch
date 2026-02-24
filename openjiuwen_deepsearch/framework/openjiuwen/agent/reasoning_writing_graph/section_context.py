# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Union

from pydantic import BaseModel, Field

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Plan, Outline, SubReportContent


class SectionBaseContext(BaseModel):
    language: str = Field(default="", description="语言")
    messages: list = Field(default=[], description="消息列表")
    section_idx: int | str = Field(default=0, description="章节索引")


class SectionContext(SectionBaseContext):
    # planner node
    plan_executed_num: int = Field(default=0, description="章节plan执行次数")
    current_plan: Plan = Field(default=None, description="当前执行的计划")
    collected_doc_num: int = Field(default=0, description="章节收集的文档数量")
    history_plans: List[Plan] = Field(default_factory=list, description="多轮规划后，历史Plan信息")

    # sub report node
    current_outline: Union[Outline, dict, str, None] = Field(default=None, description="当前章节大纲")
    section_task: str = Field(default="", description="章节任务")
    section_description: str = Field(default="", description="章节描述")
    section_iscore: bool = Field(default=False, description="是否为核心章节")
    report_task: str = Field(default="", description="报告任务")
    report_template: str = Field(default="", description="子报告模板")
    sub_report_content: SubReportContent = Field(default_factory=SubReportContent,
                                                 description="子报告内容：包含子报告的内容及其相关溯源信息")

    # 章节任务推理、写作过程中的错误和警告
    warning_infos: List[str] = Field(default_factory=list, description="推理写作子图执行过程中的警告信息")
    exception_infos: List[str] = Field(default_factory=list, description="推理写作子图执行过程中的异常信息，导致子图END")
