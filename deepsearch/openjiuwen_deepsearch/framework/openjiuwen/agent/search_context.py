# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from datetime import date
import json
import logging
from typing import List, Optional, Dict, Union, Any, Literal

from pydantic import BaseModel, Field, model_validator

from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import (
    TEMPORAL_SCOPE_SEARCH_ENGINES,
)

logger = logging.getLogger(__name__)


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
    primary_engine: str = Field(default="", description="Primary web search engine for this query.")
    secondary_engines: List[str] = Field(
        default_factory=list,
        description="Ordered scholarly search engines for this query.",
    )
    max_full_text_results: int = Field(
        default=1,
        ge=0,
        description="Maximum documents resolved to full text in stable order after query-level fusion.",
    )
    scholarly_full_text_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Shared query-wide full-text policy used after scholarly fusion.",
    )
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
    format_requirements: List[str] = Field(default_factory=list, description="章节级输出格式要求，例如表格、列名、逐项枚举、字数或样式约束")
    is_core_section: bool = Field(default=False, description="是否为重点章节")
    parent_ids: List[str] = Field(default_factory=list, description="章节执行的依赖章节")
    relationships: List[str] = Field(default_factory=list, description="章节和所依赖章节之间的关系")
    plans: List[Plan] = Field(default_factory=list, description="章节执行规划的Plan")
    section_focus: str = Field(
        default="",
        description=(
            "章节的分析职责标签，如 market_size_and_growth, vendors_and_supply, "
            "risks_and_barriers, recommendation_and_ranking"
        ),
    )
    focus_dimensions: List[str] = Field(default_factory=list, description="该章节应主展开的 2-4 个分析维度，其他章节不应深入展开这些维度")
    doc_selection_debug: Optional[Dict] = Field(
        default=None,
        description=(
            "段落选择调试信息："
            "rationales/doc_filter/coverage_matrix/dimension_scores/"
            "passage_info_map/selected_passages"
        ),
    )


class Outline(BaseModel):
    """
    大纲模型：研究报告的大纲结构
    """
    id: str = Field(default="", description="大纲的唯一标识符")
    language: str = Field(default="zh-CN", description="用户语言：zh-CN、en-US等")
    thought: str = Field(..., description="研究报告大纲背后的思考过程")
    title: str = Field(..., description="研究报告标题，概括整体研究步骤")
    sections: List[Section] = Field(default_factory=list, description="最终研究报告的章节")


class ChapterSidecar(BaseModel):
    """章节结构化汇总信息，供 Reporter 复用。"""

    chapter_summary: str = Field(default="", description="章节摘要")
    key_findings: List[str] = Field(default_factory=list, description="章节关键发现")
    risk_points: List[str] = Field(default_factory=list, description="风险、限制或不确定性")


class SubReportContent(BaseModel):
    """
    子报告内容模型：包含子报告的核心内容及其相关溯源信息
    """
    classified_content: List[Dict] = Field(default_factory=list, description="子章节筛选的文档信息")
    sub_report_content_text: str = Field(default="", description="子报告内容文本")
    sub_report_content_summary: str = Field(default="", description="子报告内容的总结")
    sub_report_chapter_sidecar: Optional[ChapterSidecar] = Field(
        default=None,
        description="子报告结构化 sidecar 汇总信息",
    )
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
    infer_messages: List[Dict] = Field(default_factory=list, description="溯源推理信息")
    chart_messages: List[Dict] = Field(default_factory=list, description="vlm图表生成信息")
    workflow_llm_token_usage: Dict[str, Any] = Field(
        default_factory=dict,
        description="本次 workflow 的 LLM token 消耗汇总，包含总量与 agent_name 维度统计",
    )
    warning_info: str = Field(default="", description="主图WorkFlow执行过程中的告警信息")
    exception_info: str = Field(default="", description="主图WorkFlow异常退出时的异常信息")


class OutlineInteraction(BaseModel):
    """
    大纲交互模型：用户与系统进行大纲交互时的输入输出结构
    """
    feedback: str = Field(default="", description="用户反馈")
    interaction_mode: str = Field(default="", description="大纲交互模式: revise_comment, revise_outline")
    outline_before: Union[Outline, Dict, str, None] = Field(default=None, description="用户反馈前的outline")


class ReportTypePolicy(BaseModel):
    """
    报告类型策略：由 research_intent.report_type 归一化后解析得到的确定性配置。
    """

    report_type: Literal["professional", "brief"] = Field(
        default="professional", description="报告类型：professional（专业版）或 brief（精简版）"
    )
    paragraph_style: Literal["concise", "detailed"] = Field(
        default="detailed", description="段落风格"
    )
    require_summary_first: bool = Field(
        default=False, description="大纲/规划是否强调先摘要/总览"
    )
    require_methodology_and_risk: bool = Field(
        default=False, description="大纲/规划是否强调方法论与风险"
    )


class TemporalScope(BaseModel):
    """用户明确指定的研究时间范围。

    Attributes:
        constraint_type: 时间约束对象；source_date 限制来源发布日期，content_date 限制事实或数据时间。
        start_date: 包含边界的开始日期。
        end_date: 包含边界的结束日期。
    """

    constraint_type: Literal["source_date", "content_date"] = Field(description="时间约束对象")
    start_date: Optional[date] = Field(default=None, description="包含边界的开始日期")
    end_date: Optional[date] = Field(default=None, description="包含边界的结束日期")

    @model_validator(mode="after")
    def validate_date_range(self) -> "TemporalScope":
        """校验时间范围至少包含一个边界且顺序有效。

        Returns:
            校验通过的时间范围。

        Raises:
            ValueError: 未提供任何边界，或开始日期晚于结束日期时抛出。
        """
        if self.start_date is None and self.end_date is None:
            raise ValueError("temporal scope requires at least one date boundary")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("temporal scope start_date must not be after end_date")
        return self


class TargetPaper(BaseModel):
    """User-supplied exact identifiers or implicit clues for one paper."""

    title: str = ""
    pmid: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    dataset: str = ""
    data_year: str = ""
    topic: str = ""

    @model_validator(mode="after")
    def require_one_clue(self) -> "TargetPaper":
        """Strip clue values and reject an item that carries no usable clue."""
        field_names = ("title", "pmid", "doi", "arxiv_id", "url", "dataset", "data_year", "topic")
        for field_name in field_names:
            setattr(self, field_name, str(getattr(self, field_name) or "").strip())
        if not any(getattr(self, field_name) for field_name in field_names):
            raise ValueError("target paper requires at least one clue")
        return self


class ResearchIntent(BaseModel):
    """报告生成意图：从用户 query 中解析出的结构化约束。"""
    task_type: Optional[str] = Field(default=None, description="任务类型，如 comparison/classification/trend_judgement")
    required_dimensions: List[str] = Field(default_factory=list, description="必须覆盖的比较或分析维度")
    comparison_targets: List[str] = Field(default_factory=list, description="需要显式比较的对象")
    section_count: Optional[int] = Field(default=None, description="用户希望的章节数量")
    audience_role: Optional[str] = Field(default=None, description="目标读者角色")
    tone: Optional[str] = Field(default=None, description="写作风格，建议使用稳定英文枚举值")
    report_type: Optional[Literal["professional", "brief"]] = Field(default=None, description="报告类型")
    include_url: List[str] = Field(default_factory=list, description="用户指定包含的链接")
    exclude_url: List[str] = Field(default_factory=list, description="用户指定排除的链接")
    exclude_titles: List[str] = Field(default_factory=list, description="用户指定排除的文章标题")
    include_domains: List[str] = Field(default_factory=list, description="用户指定的站点域名")
    exclude_domains: List[str] = Field(default_factory=list, description="用户排除的站点域名")
    temporal_scope: Optional[TemporalScope] = Field(
        default=None,
        description="[deprecated] 旧单值字段,仅兼容旧序列化 state 的 model_validate 输入路由;"
                    "读改用 source_date_scope/content_date_scope. 始终 None(post-construction).",
    )
    source_date_scope: Optional[TemporalScope] = Field(
        default=None, description="来源发表/可得时间约束（硬门）"
    )
    content_date_scope: Optional[TemporalScope] = Field(
        default=None, description="事实/事件/研究/数据时段约束（软分）"
    )
    target_papers: List[TargetPaper] = Field(
        default_factory=list,
        description="Papers explicitly identified or implicitly described by the user.",
    )

    @model_validator(mode="before")
    @classmethod
    def _route_legacy_temporal_scope(cls, data):
        if isinstance(data, BaseModel):
            data = data.model_dump()
        if not isinstance(data, dict):
            return data
        has_new = data.get("source_date_scope") is not None or data.get("content_date_scope") is not None
        legacy = data.get("temporal_scope")
        if legacy is not None and not has_new:
            # Route dict-form AND TemporalScope-instance-form legacy by constraint_type before pop,
            # else instance-form ResearchIntent(temporal_scope=<TemporalScope>) would lose the
            # constraint (source_date_scope stays None, temporal_scope popped).
            ct = legacy.get("constraint_type") if isinstance(legacy, dict) else getattr(legacy, "constraint_type", None)
            if ct == "source_date":
                data["source_date_scope"] = legacy
            elif ct == "content_date":
                data["content_date_scope"] = legacy
        if legacy is not None:
            # Pop the legacy key — all readers use source_date_scope/content_date_scope.
            # The deprecated temporal_scope field def stays only to absorb old serialized state.
            data.pop("temporal_scope", None)
        return data

    @model_validator(mode="after")
    def _check_scope_type_consistency(self) -> "ResearchIntent":
        if self.source_date_scope and self.source_date_scope.constraint_type != "source_date":
            logger.warning(
                "source_date_scope.constraint_type=%s mismatch, dropping",
                self.source_date_scope.constraint_type,
            )
            self.source_date_scope = None
        if self.content_date_scope and self.content_date_scope.constraint_type != "content_date":
            logger.warning(
                "content_date_scope.constraint_type=%s mismatch, dropping",
                self.content_date_scope.constraint_type,
            )
            self.content_date_scope = None
        return self


def build_target_papers_prompt_context(
    intent: ResearchIntent | dict | None,
    evidence_ledger: dict | None = None,
) -> dict:
    """Serialize target-paper constraints for collector prompts."""
    if intent is None:
        papers = []
    else:
        model = intent if isinstance(intent, ResearchIntent) else ResearchIntent.model_validate(intent)
        papers = [paper.model_dump() for paper in model.target_papers]
    if evidence_ledger is not None:
        from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
            target_papers_still_searchable,
        )
        papers = target_papers_still_searchable(papers, evidence_ledger)
    return {
        "has_target_papers": bool(papers),
        "target_papers": papers,
        "target_papers_text": json.dumps(papers, ensure_ascii=False),
    }


class SectionLocalContract(BaseModel):
    """章节级局部合同：限制章节只覆盖自己负责的分析职责。"""

    section_focus: str = Field(default="", description="当前章节的核心职责标签")
    allowed_dimensions: List[str] = Field(default_factory=list, description="允许主展开的维度")
    is_final_decision_section: bool = Field(default=False, description="是否承担最终判断/排序职责")


def build_research_intent_prompt_context(intent: ResearchIntent | dict | None) -> dict:
    """将 ResearchIntent 规范化为 prompt 可直接消费的上下文字段。"""
    if intent is None:
        model = ResearchIntent()
    elif isinstance(intent, ResearchIntent):
        model = intent
    else:
        model = ResearchIntent.model_validate(intent)

    required_dimensions = [item for item in model.required_dimensions if item]
    comparison_targets = [item for item in model.comparison_targets if item]

    return {
        "task_type": model.task_type or "",
        "required_dimensions": required_dimensions,
        "required_dimensions_text": ", ".join(required_dimensions),
        "comparison_targets": comparison_targets,
        "comparison_targets_text": ", ".join(comparison_targets),
        "has_required_dimensions": bool(required_dimensions),
        "has_comparison_targets": bool(comparison_targets),
    }


def resolve_temporal_embed_in_query(
    engine_name: str | None,
    source_date_scope: "TemporalScope | None",
    content_date_scope: "TemporalScope | None",
    scholarly_enabled: bool = False,
) -> bool:
    """按 引擎×双 scope 决定搜索词是否带约束时间词。副引擎启用时强制带。

    content_date 约束表达的是事实发生时间，任何引擎都无法原生过滤，始终需要写进
    搜索词；source_date 约束在 Tavily 等原生支持时间过滤的引擎上可由引擎原生过滤，
    搜索词无需再带约束时间词，其余引擎（或副引擎启用时）需把约束时间词写进搜索词。
    """
    if content_date_scope is not None:
        return True
    if source_date_scope is not None:
        if scholarly_enabled:
            return True
        return (engine_name or "") not in TEMPORAL_SCOPE_SEARCH_ENGINES
    return False


def build_temporal_scope_prompt_context(
    intent: ResearchIntent | dict | None,
    *,
    engine_name: str | None = None,
    scholarly_enabled: bool = False,
) -> dict:
    """将时间约束转换为研究阶段 prompt 可直接消费的上下文（双指令 + 拼接兼容）。

    Args:
        intent: 结构化研究意图或兼容字典。支持新形（``source_date_scope``/
            ``content_date_scope``）、旧形实例（``temporal_scope=<TemporalScope>``）与
            旧形字典（``{"temporal_scope": {...}}``）三类构造，经 resolver 统一取值。
        engine_name: 主 web 搜索引擎名，用于判断引擎是否原生支持时间过滤。
        scholarly_enabled: 副引擎（学术搜索）是否启用，启用时强制在搜索词中带约束时间词。

    Returns:
        source/content 两条独立指令（``source_date_instruction``/
        ``content_date_instruction``）、拼接兼容字段 ``temporal_scope_instruction``（供
        旧 Prompt 直接消费）、embed 决策与合并后的 ``temporal_query_instruction``；
        无约束时返回空字段。
    """
    # 用 resolver 取双 scope：统一覆盖新形、实例旧形、字典旧形三类构造,
    # 避免 bare field 读漏取（实例旧形经 before-validator 已路由到新键并 pop）。
    sds = _resolve_source_date_scope(intent)
    cds = _resolve_content_date_scope(intent)

    if sds is None and cds is None:
        # 与有约束分支保持同形 6 键，消费方可无条件按键读取。
        return {
            "has_temporal_scope": False,
            "source_date_instruction": "",
            "content_date_instruction": "",
            "temporal_scope_instruction": "",
            "temporal_embed_in_query": False,
            "temporal_query_instruction": "",
        }

    def _boundary(scope: "TemporalScope") -> str:
        start_date = scope.start_date.isoformat() if scope.start_date else ""
        end_date = scope.end_date.isoformat() if scope.end_date else ""
        if start_date and end_date:
            return f"from {start_date} through {end_date}"
        if start_date:
            return f"on or after {start_date}"
        return f"on or before {end_date}"

    parts = []
    src_instr = ""
    cds_instr = ""
    if sds is not None:
        src_instr = (
            f"Use sources published {_boundary(sds)}, using inclusive boundary dates."
        )
        parts.append(src_instr)
    if cds is not None:
        cds_instr = (
            f"Keep the facts and data {_boundary(cds)}, using inclusive boundary dates."
        )
        parts.append(cds_instr)

    embed = resolve_temporal_embed_in_query(
        engine_name, sds, cds, scholarly_enabled
    )
    qis = []
    if embed:
        if cds is not None:
            qis.append(
                "Express this boundary naturally in every query as a constraint time phrase "
                "tied to when the facts/events occurred (e.g. 'events in 2018', '2018 research'), "
                "not the publication date. Keep topical years that are part of the research "
                "subject — do NOT drop them."
            )
        if sds is not None and (
            (engine_name or "") not in TEMPORAL_SCOPE_SEARCH_ENGINES or scholarly_enabled
        ):
            qis.append(
                "Express this boundary naturally in every query as a constraint time phrase "
                "tied to the publication date (e.g. 'published in 2024'). Keep topical years that "
                "are part of the research subject — do NOT drop them."
            )
    else:
        qis.append(
            "Do NOT add any constraint time phrase (the engine filters by date natively). "
            "Topical years that are part of the research subject are still allowed."
        )
    query_instruction = " ".join(qis)

    return {
        "has_temporal_scope": sds is not None or cds is not None,
        "source_date_instruction": src_instr,
        "content_date_instruction": cds_instr,
        "temporal_scope_instruction": " ".join(parts),  # 兼容旧 Prompt 拼接字段
        "temporal_embed_in_query": embed,
        "temporal_query_instruction": query_instruction,
    }


def _coerce_scope(raw: dict | TemporalScope | None, kind: str) -> TemporalScope | None:
    """构造 TemporalScope 并强制 constraint_type=kind；非法返回 None（静默丢弃先例）。

    用 model_validate 兼顾 date 对象（model_dump python 模式）与 ISO 字符串（JSON state）。
    """
    if isinstance(raw, TemporalScope):
        raw = {"constraint_type": raw.constraint_type, "start_date": raw.start_date, "end_date": raw.end_date}
    if not isinstance(raw, dict):
        return None
    payload = {**raw, "constraint_type": kind}
    try:
        return TemporalScope.model_validate(payload)
    except Exception:
        return None


def _resolve_source_date_scope(research_intent: ResearchIntent | dict | None) -> TemporalScope | None:
    """取 source_date 约束；实例直接读新键（before-validator 已路由实例形旧键），dict 新键优先、旧 temporal_scope 兜底（兼容升级前持久化 state）。"""
    if research_intent is None:
        return None
    if isinstance(research_intent, ResearchIntent):
        return research_intent.source_date_scope
    if isinstance(research_intent, dict):
        sds = research_intent.get("source_date_scope")
        if sds is not None:
            return _coerce_scope(sds, "source_date")
        legacy = research_intent.get("temporal_scope")
        if isinstance(legacy, dict) and legacy.get("constraint_type") == "source_date":
            return _coerce_scope(legacy, "source_date")
    return None


def _resolve_content_date_scope(research_intent: ResearchIntent | dict | None) -> TemporalScope | None:
    """取 content_date 约束；实例直接读新键（before-validator 已路由实例形旧键），dict 新键优先、旧 temporal_scope 兜底（兼容升级前持久化 state）。"""
    if research_intent is None:
        return None
    if isinstance(research_intent, ResearchIntent):
        return research_intent.content_date_scope
    if isinstance(research_intent, dict):
        cds = research_intent.get("content_date_scope")
        if cds is not None:
            return _coerce_scope(cds, "content_date")
        legacy = research_intent.get("temporal_scope")
        if isinstance(legacy, dict) and legacy.get("constraint_type") == "content_date":
            return _coerce_scope(legacy, "content_date")
    return None


def build_section_local_contract_prompt_context(contract: SectionLocalContract | dict | None) -> dict:
    """将 SectionLocalContract 规范化为 prompt 可直接消费的上下文字段。"""
    if contract is None:
        model = SectionLocalContract()
    elif isinstance(contract, SectionLocalContract):
        model = contract
    else:
        model = SectionLocalContract.model_validate(contract)

    allowed_dimensions = [item for item in model.allowed_dimensions if item]

    return {
        "section_focus": model.section_focus or "",
        "allowed_dimensions": allowed_dimensions,
        "allowed_dimensions_text": ", ".join(allowed_dimensions),
        "is_final_decision_section": model.is_final_decision_section,
        "has_allowed_dimensions": bool(allowed_dimensions),
    }


class SearchContext(BaseModel):
    """
    上下文状态模型：工作流运行时的状态上下文
    """
    # 1、入参或必需字段
    session_id: str = Field(default="", description="会话ID")
    original_query: str = Field(default="", description="用户输入的原始问题")
    research_intent: ResearchIntent = Field(default_factory=ResearchIntent, description="结构化报告约束")
    report_type_policy: ReportTypePolicy = Field(
        default_factory=ReportTypePolicy,
        description="报告类型策略，意图识别后确认，供大纲/规划/收集/写作消费",
    )
    brief_state: Dict[str, Any] | None = Field(
        default=None,
        description="独立 Brief 工作流的序列化状态；专业版保持为空。",
    )
    messages: List[Message] = Field(default_factory=list, description="对话消息列表")
    language: str = Field(default="zh-CN", description="语言")
    report_template: str = Field(default="", description="模板内容")
    search_mode: str = Field(default="research", description="搜索类型，research 或 search 对应研究或深搜模式")
    entry_search_results: List[Dict] = Field(default_factory=list, description="Entry节点预搜索结果")

    # 2、feedback相关参数
    questions: str = Field(default="", description="系统基于用户问题提出的问题")
    user_feedback: str = Field(default="", description="用户问题的反馈结果")
    outline_interactions: List[OutlineInteraction] = Field(default_factory=list, description="大纲多轮交互历史记录")
    outline_execution_method: Literal["", "parallel", "dependency_driving"] = Field(
        default="",
        description="大纲生成阶段实际选择的执行方式，空值表示尚未选择，parallel表示普通大纲，dependency_driving表示依赖驱动大纲",
    )

    # 3、运行时上下文状态存储
    outline_executed_num: int = Field(default=0, description="大纲执行次数")
    current_outline: Union[Outline, Dict, str, None] = None
    history_outlines: List[Outline] = Field(default_factory=list, description="多轮大纲生成时，保存历史outline列表")
    report_generated_num: int = Field(default=0, description="报告生成次数")
    current_report: Union[Report, Dict, str, None] = None
    history_reports: List[Report] = Field(default_factory=list, description="多轮报告生成时，保存历史report列表")

    # 4、用户反馈优化相关参数
    feedback_interaction_count: int = Field(default=0, description="用户反馈优化交互次数")
    feedback_snapshot_sent: bool = Field(default=False, description="是否已向前端推送初始反馈快照")
    rewrite_history: List[Dict] = Field(default_factory=list, description="用户反馈优化历史记录")

    # 5、其他参数
    final_result: FinalResult = Field(default_factory=FinalResult, description="最终返回前端的结果")
    debug_pre_node: str = Field(default="", description="添加格式化debug日志的前一节点")


class ValidationResult(Enum):
    passed: str = "pass"
    pending: str = "pending"
    failed: str = "fail"


class SearchFinalResult(BaseModel):
    question: str
    termination: str
    completion_time: float
    current_date_time: str
    prediction: str | None = None
    gold_answer: str | None = Field(
        default=None,
        description="Optional gold label from benchmark JSON (for final_result.json comparison).",
    )
    messages: List[Dict] | None = None
    config: Dict | None = None
    retrieved_evidence_ids: List[Any] = Field(default_factory=list)


class VerifiedClueResult(BaseModel):
    text: str
    status: ValidationResult  # pass|fail|pending
    reason: str = ""


class Variable(BaseModel):
    model_config = dict(validate_assignment=True)
    id: int
    type: str  # Ex: City
    question_clues: List[
        str
    ]  # Clues derived from the question itself (immutable after initialization)
    discovered_clues: List[
        str
    ]  # Clues discovered during research (can be added incrementally)
    candidate: Optional[str]
    candidate_strength: Optional[float] = Field(
        ge=0, le=1
    )  # An optional estimate produced by the llm on "How likely the candidate is to the the true value"


class State(BaseModel):
    state: List[Variable]
    depth: int = 0
    id: str = '0'
    retrieved_evidence_ids: List[Any] = Field(default_factory=list)
    answer_variable: int
    verify_result: Optional[List["VerifiedVariableResult"]] = None

    def __str__(self):
        return f"State(state={self.state})"

    def __repr__(self):
        return f"State(state={self.state}, depth={self.depth}, id={self.id}, answer_variable={self.answer_variable})"

    def str_to_verify_result(self):
        return f"State(state={self.state}, verify_result={self.verify_result})"


class CandidateVerifiedClues(BaseModel):
    """
    Structured verification report returned by the verifier tool.

    Expected shape (from verifier tool):
    {
      "value": "...",
      "clues": [{"text": "...", "status": "pass|fail|pending", "reason": "..."}],
      "overall": "pass|fail|pending",
      "evidence": "..."
    }
    """

    value: Optional[str] = None
    clues: List[VerifiedClueResult] = Field(default_factory=list)
    overall: Optional[ValidationResult] = None
    evidence: Optional[str] = None


class VerifiedVariableResult(BaseModel):
    id: int
    type: str
    candidate_verified_clues: CandidateVerifiedClues = Field(
        default_factory=CandidateVerifiedClues
    )


class Result(BaseModel):
    messages: List[Dict]
    previous_action_id: str
    new_states: List[State]
    found_answer: str | None
    summary: str | None = None
    retrieved_evidence_ids: List[Any] = Field(default_factory=list)

    def __str__(self):
        return f"Result(messages={self.messages}, new_states={self.new_states}, found_answer={self.found_answer})"

    def __repr__(self):
        return (
            "Result("
            f"messages={self.messages}, "
            f"new_states={self.new_states}, "
            f"found_answer={self.found_answer}, "
            f"retrieved_evidence_ids={self.retrieved_evidence_ids}"
            ")"
        )
    
    def get_summary(self):
        if self.summary:
            return f"Research Agent's summary: {self.summary}"
        else:
            last_entry = self.messages[-1]
            last_msg = last_entry.get("content", str(last_entry)) if isinstance(last_entry, dict) else str(last_entry)
            return f"Research Agent's final message output: {last_msg[:500]}"




class ActionProposal(BaseModel):
    direction: str
    score: float = Field(ge=0, le=1)


class Action(BaseModel):
    id: str
    question: str
    state: State
    proposal: ActionProposal
    messages: List[Dict]
