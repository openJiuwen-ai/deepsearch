"""Brief 独立工作流使用的强类型数据契约。"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class EvidenceType(str, Enum):
    """章节研究步骤所需的证据类型。"""

    DATA = "data"
    COMPARISON = "comparison"
    TIMELINE = "timeline"
    POLICY = "policy"
    CASE = "case"
    GENERAL = "general"


class OutputFormat(str, Enum):
    """Brief 章节允许的输出形式。"""

    PARAGRAPH = "paragraph"
    BULLETS = "bullets"
    TABLE = "table"
    TIMELINE = "timeline"


class CoverageStatus(str, Enum):
    """研究步骤的证据覆盖状态。"""

    COVERED = "covered"
    WEAK = "weak"
    MISSING = "missing"
    UNKNOWN = "unknown"


class BriefResearchStep(BaseModel):
    """Brief 章节内一个可验证的研究步骤。"""

    id: str
    requirement: str = Field(min_length=2, max_length=240)
    evidence_type: EvidenceType = EvidenceType.GENERAL


class BriefSection(BaseModel):
    """Brief 大纲中的一个精简章节。"""

    id: str
    title: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=2, max_length=240)
    research_steps: list[BriefResearchStep] = Field(min_length=2, max_length=4)
    output_formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.PARAGRAPH])
    format_note: str = Field(default="", max_length=240)


class BriefOutline(BaseModel):
    """Brief 报告的大纲及其章节集合。"""

    title: str = Field(min_length=1, max_length=160)
    sections: list[BriefSection] = Field(min_length=2)


class BriefOutlineRequest(BaseModel):
    """生成 Brief 精简大纲所需的用户输入。"""

    query: str = Field(min_length=1)
    language: str = "zh-CN"
    research_intent: dict[str, Any] = Field(default_factory=dict)
    audience_role: str = ""
    tone: str = ""
    clarification_questions: str = ""
    user_feedback: str = ""
    report_template: str = ""


class BriefQuery(BaseModel):
    """面向一个或多个章节步骤的搜索查询。"""

    query: str = Field(min_length=2, max_length=500)
    section_ids: list[str] = Field(min_length=1)
    step_ids: list[str] = Field(min_length=1)


class BriefQueryRequest(BaseModel):
    """生成正式或补充 Brief Query 的输入。"""

    outline: BriefOutline
    user_query: str
    research_intent: dict[str, Any] = Field(default_factory=dict)
    executed_queries: list[str] = Field(default_factory=list)
    blocking_gaps: list["BriefStepCoverage"] = Field(default_factory=list)


class BriefSearchResult(BaseModel):
    """未经网页抓取的搜索结果标准化记录。"""

    source_id: str
    title: str
    url: str
    source: str = ""
    publish_time: str = ""
    snippet: str
    search_rank: int = Field(ge=1)
    section_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)


class BriefCollectionContext(BaseModel):
    """审阅与一次补搜之间保留的运行时搜索上下文。"""

    executed_queries: list[str] = Field(default_factory=list)
    search_results: list[BriefSearchResult] = Field(default_factory=list)


class BriefSelectedDoc(BaseModel):
    """被章节评估选中的搜索结果及其步骤路由。"""

    source_id: str
    step_ids: list[str]
    evaluation_rank: int = Field(ge=1)


class BriefStepCoverage(BaseModel):
    """单一研究步骤的覆盖状态与阻断缺口。"""

    step_id: str
    status: CoverageStatus
    reason: str = Field(max_length=240)
    blocking_gap: bool = False
    gap_description: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def clear_invalid_blocking_gap(self) -> "BriefStepCoverage":
        """清除已覆盖步骤中无效的阻断缺口标记。

        Returns:
            规范化后的步骤覆盖记录。
        """
        if self.status == CoverageStatus.COVERED:
            self.blocking_gap = False
            self.gap_description = ""
        return self


class BriefSectionWritingGuidance(BaseModel):
    """某个章节的内部写作侧重点。"""

    section_id: str
    guidance: str


class BriefWritingGuidance(BaseModel):
    """审阅节点给正文和摘要使用的内部编辑指引。"""

    report_strategy: str = ""
    section_guidance: list[BriefSectionWritingGuidance] = Field(default_factory=list)


class BriefEvidenceReview(BaseModel):
    """首轮证据审阅结论：写作指引及一次补搜所需缺口。"""

    writing_guidance: BriefWritingGuidance = Field(default_factory=BriefWritingGuidance)
    blocking_gaps: list[BriefStepCoverage] = Field(default_factory=list)


class BriefSectionEvidence(BaseModel):
    """一个章节的选文与研究步骤覆盖情况。"""

    selected_docs: list[BriefSelectedDoc] = Field(default_factory=list)
    coverage: list[BriefStepCoverage] = Field(default_factory=list)


class BriefCitationRecord(BaseModel):
    """按全报告唯一 URL 分配的引用注册表记录。"""

    source_id: str
    index: int = Field(ge=1)
    title: str
    url: str
    original_content: str


class BriefChapter(BaseModel):
    """章节写作结果。"""

    section_id: str
    raw_markdown: str


class BriefCollectionResult(BaseModel):
    """Brief 采集阶段交给写作和拼装阶段的证据与引用状态。"""

    section_evidence: dict[str, BriefSectionEvidence]
    citation_registry: list[BriefCitationRecord]


class BriefCollectorRequest(BaseModel):
    """Brief 证据评估所需的依赖与输入。"""

    model_config = {"arbitrary_types_allowed": True}

    outline: BriefOutline
    user_query: str
    research_intent: dict[str, Any] = Field(default_factory=dict)
    llm: Any


class BriefReviewRequest(BaseModel):
    """审阅 Brief 首轮证据并生成内部写作指引所需的输入。"""

    model_config = {"arbitrary_types_allowed": True}

    outline: BriefOutline
    collection: BriefCollectionResult
    llm: Any
    audience_role: str = ""
    tone: str = ""
    user_format: str = ""


class BriefWritingRequest(BaseModel):
    """并行生成 Brief 章节所需的依赖与输入。"""

    model_config = {"arbitrary_types_allowed": True}

    llm: Any
    outline: BriefOutline
    collection: BriefCollectionResult
    language: str = "zh-CN"
    audience_role: str = ""
    tone: str = ""
    user_format: str = ""
    writing_guidance: BriefWritingGuidance | None = None


class BriefWritingEvidence(BaseModel):
    """传入单章写作 Prompt 的已裁剪证据包。"""

    documents: list[dict[str, Any]]
    coverage: list[BriefStepCoverage]


class BriefSummaryRequest(BaseModel):
    """生成 Brief 顶部核心摘要的输入。"""
    model_config = {"arbitrary_types_allowed": True}
    llm: Any
    title: str
    language: str
    chapters: list[BriefChapter]
    section_evidence: dict[str, BriefSectionEvidence]
    citation_registry: list[BriefCitationRecord]
    audience_role: str = ""
    tone: str = ""
    user_format: str = ""
    writing_guidance: BriefWritingGuidance | None = None
    max_retries: int | None = None


class BriefReportAssembly(BaseModel):
    """最终 Brief 报告及顺序对齐后的溯源数据。"""
    report_content: str
    merged_trace_source_datas: list[dict[str, Any]]


class BriefAssemblyRequest(BaseModel):
    """稳定拼装最终 Brief 报告的输入。"""
    title: str
    language: str
    executive_summary: str
    chapters: list[BriefChapter]
    citation_registry: list[BriefCitationRecord]
    section_order: dict[str, int]


class BriefWorkflowState(BaseModel):
    """在 SearchContext 中保存的独立 Brief 工作流状态。"""

    outline: BriefOutline | None = None
    collection: BriefCollectionResult | None = None
    collection_context: BriefCollectionContext | None = None
    evidence_review: BriefEvidenceReview | None = None
    chapters: list[BriefChapter] = Field(default_factory=list)
    executive_summary: str = ""
