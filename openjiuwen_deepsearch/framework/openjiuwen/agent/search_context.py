# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from typing import List, Optional, Dict, Union

from pydantic import BaseModel, Field


class Message(BaseModel):
    """
    消息模型：对话消息结构
    """
    name: Optional[str] = Field(default=None, description="消息名称")
    role: str = Field(..., description="消息角色，例如 user / system / assistant")
    content: str = Field(..., description="消息内容")


class StepType(str, Enum):
    """
    步骤类型枚举
    """
    INFO_COLLECTING = "info_collecting"


class RetrievalQuery(BaseModel):
    """
    检索query模型：步骤任务的检索query
    """
    query: str = Field(..., description="直接用于检索的query")
    description: str = Field(default="", description="简要说明query为何与搜索任务相关，为何要生成当前query")
    doc_infos: Optional[List[Dict]] = Field(default_factory=list, description="query检索的文档信息")


class Step(BaseModel):
    """
    步骤模型：章节计划中的具体步骤
    """
    id: str = Field(default="", description="步骤的唯一标识符")
    type: StepType = Field(..., description="步骤类型（枚举值）")
    title: str = Field(..., description="步骤标题，简要描述步骤内容")
    description: str = Field(..., description="步骤详细说明，明确指定需要收集的数据或执行的编程步骤")
    parent_ids: Optional[List[str]] = Field(default_factory=list, description="步骤执行的依赖步骤")
    relationships: Optional[List[str]] = Field(default_factory=list, description="步骤和所依赖步骤之间的关系")
    background_knowledge: Optional[List[str]] = Field(default_factory=list, description="步骤执行所需要的背景知识")
    retrieval_queries: Optional[List[RetrievalQuery]] = Field(default_factory=list, description="步骤的query信息列表")
    step_result: Optional[str] = Field(default=None, description="步骤执行的总结结果，完成后由系统进行填充")
    evaluation: Optional[str] = Field(default="", description="步骤执行结果的评估")


class Plan(BaseModel):
    """
    计划模型：完成章节任务所需的完整计划
    """
    id: str = Field(default="", description="plan的唯一标识符")
    language: str = Field(default="zh-CN", description="用户语言：zh-CN、en-US等")
    title: str = Field(..., description="计划标题，概括整体目标")
    thought: str = Field(..., description="计划背后的思考过程，解释步骤顺序和选择的理由")
    is_research_completed: bool = Field(..., description="是否已完成信息收集工作")
    steps: List[Step] = Field(default_factory=list, description="info_collecting类型的步骤")
    background_knowledge: Optional[Dict[str, str]] = Field(default_factory=dict, description="plan执行所需要的背景知识")


class Section(BaseModel):
    """
    章节模型：研究报告大纲结构中的具体章节
    """
    id: str = Field(default="", description="章节的唯一标识符")
    title: str = Field(..., description="章节标题，概括本章节整体目标")
    description: str = Field(..., description="章节研究步骤详细说明，明确指定需要收集的数据或执行的编程步骤")
    is_core_section: bool = Field(default=False, description="是否为重点章节")
    parent_ids: List[str] = Field(default_factory=list, description="章节执行的依赖章节")
    relationships: List[str] = Field(default_factory=list, description="章节和所依赖章节之间的关系")
    plans: List[Plan] = Field(default_factory=list, description="章节执行规划的Plan")


class Outline(BaseModel):
    """
    大纲模型：研究报告的大纲结构
    """
    id: str = Field(default="", description="大纲的唯一标识符")
    language: str = Field(default="zh-CN", description="用户语言：zh-CN、en-US等")
    thought: str = Field(..., description="研究报告大纲背后的思考过程")
    title: str = Field(..., description="研究报告标题，概括整体研究步骤")
    sections: List[Section] = Field(default_factory=list, description="最终研究报告的章节")


class SubReportContent(BaseModel):
    """
    子报告内容模型：包含子报告的核心内容及其相关溯源信息
    """
    classified_content: List[Dict] = Field(default_factory=list, description="子章节筛选的文档信息")
    sub_report_content_text: str = Field(default="", description="子报告内容文本")
    sub_report_content_summary: str = Field(default="", description="子报告内容的总结")
    sub_report_trace_source_datas: List[Dict] = Field(default_factory=list, description="子报告的溯源信息")


class SubReport(BaseModel):
    """
    子报告模型：研究报告的子报告，子报告按照大纲结构撰写
    """
    id: str = Field(default="", description="子报告的唯一标识符")
    section_id: str | int = Field(default="1", description="子章节id")
    section_task: str = Field(default="", description="子章节报告标题")
    background_knowledge: Optional[List[Dict]] = Field(default_factory=list, description="子报告执行所需要的背景知识")
    content: SubReportContent = Field(default_factory=SubReportContent, description="子报告的内容部分")


class Report(BaseModel):
    """
    报告模型：研究报告
    """
    id: str = Field(default="", description="总报告的唯一标识符")
    # report节点的输入
    report_task: str = Field(default="", description="总报告任务")
    report_template: str = Field(default="", description="总报告模板")
    sub_reports: List[SubReport] = Field(default_factory=list, description="各章节的子报告执行信息")

    # report节点的输出
    report_content: str = Field(default="", description="ReportNode输出的溯源之前的总报告全文内容")
    all_classified_contents: List[List] = Field(default_factory=list,
                                                description="所有子章节筛选后的文档信息重排序后的内容")

    # 溯源生成节点的输出
    merged_trace_source_datas: List[Dict] = Field(default_factory=list, description="溯源校验前的溯源信息，用于溯源校验")

    # 溯源校验节点的输出
    checked_trace_source_report_content: str = Field(default="", description="溯源校验后生成的报告文章内容")
    checked_trace_source_datas: List[Dict] = Field(default_factory=list, description="最终溯源信息")


class FinalResult(BaseModel):
    """
    最终结果模型：Workflow结束时的状态
    """
    response_content: str = Field(default="", description="响应内容")
    citation_messages: dict = Field(default={}, description="引用信息")
    infer_messages: dict = Field(default={}, description="溯源推理信息")
    warning_info: str = Field(default="", description="主图WorkFlow执行过程中的告警信息")
    exception_info: str = Field(default="", description="主图WorkFlow异常退出时的异常信息")


class OutlineInteraction(BaseModel):
    """
    大纲交互模型：用户与系统进行大纲交互时的输入输出结构
    """
    feedback: str = Field(default="", description="用户反馈")
    interaction_mode: str = Field(default="", description="大纲交互模式: revise_comment, revise_outline")
    outline_before: Union[Outline, Dict, str, None] = Field(default=None, description="用户反馈前的outline")


class SearchContext(BaseModel):
    """
    上下文状态模型：工作流运行时的状态上下文
    """
    # 1、入参或必需字段
    session_id: str = Field(default="", description="会话ID")
    query: str = Field(default=None, description="用户输入问题")
    messages: List[Message] = Field(default_factory=list, description="对话消息列表")
    language: str = Field(default="zh-CN", description="语言")
    report_template: str = Field(default="", description="模板内容")
    search_mode: str = Field(default="research", description="搜索类型，research 或 search 对应研究或深搜模式")

    # 2、feedback相关参数
    questions: str = Field(default="", description="系统基于用户问题提出的问题")
    user_feedback: str = Field(default="", description="用户问题的反馈结果")
    outline_interactions: List[OutlineInteraction] = Field(default_factory=list, description="大纲多轮交互历史记录")

    # 3、运行时上下文状态存储
    outline_executed_num: int = Field(default=0, description="大纲执行次数")
    current_outline: Union[Outline, Dict, str, None] = None
    history_outlines: List[Outline] = Field(default_factory=list, description="多轮大纲生成时，保存历史outline列表")
    report_generated_num: int = Field(default=0, description="报告生成次数")
    current_report: Union[Report, Dict, str, None] = None
    history_reports: List[Report] = Field(default_factory=list, description="多轮报告生成时，保存历史report列表")

    # 4、其他参数
    final_result: FinalResult = Field(default_factory=FinalResult, description="最终返回前端的结果")
    debug_pre_node: str = Field(default="", description="添加格式化debug日志的前一节点")
