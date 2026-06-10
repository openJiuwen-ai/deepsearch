# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
from dataclasses import dataclass, replace

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import build_legacy_doc_infos_view
from openjiuwen_deepsearch.algorithm.report.doc_prefilter import deduplicate_doc_infos
from openjiuwen_deepsearch.algorithm.user_feedback_processor.common import (
    UserFeedbackPromptInvoker,
    resolve_model_context_collector,
    resolve_session_collector,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import strip_markup_in_range
from openjiuwen_deepsearch.algorithm.user_feedback_processor.section_locator import (
    heading_block_end,
    locate_enclosing_numbered_major_block,
    locate_section,
    parse_markdown_headings,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service import (
    CollectorExecutionService,
    CollectorRunPlanConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Plan, Step, StepType
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)

NEW_TASK_MODIFY_EXISTING_SUBSECTION = "modify_existing_subsection"
NEW_TASK_APPEND_NEW_SUBSECTION = "append_new_subsection"


@dataclass(frozen=True)
class NewTaskTargetSection:
    """用户选区对应的目标章节及清洗结果。"""

    section_title: str
    section_text: str
    clean_section_text: str
    clean_selected_text: str
    section_start_offset: int
    section_end_offset: int
    major_section_title: str = ""
    major_section_text: str = ""
    clean_major_section_text: str = ""
    major_section_start_offset: int = 0
    major_section_end_offset: int = 0
    major_heading_level: int = 0


@dataclass(frozen=True)
class SectionHistoricalAssets:
    """目标章节映射后的历史研究资产视图。"""

    section_id: str | None
    match_mode: str
    section_title: str
    current_section_text: str
    historical_plans: list
    historical_doc_infos: list


@dataclass(frozen=True)
class NewTaskAssetAssessment:
    """历史资产评估结果。"""

    relevant_doc_infos: list
    is_sufficient: bool
    missing_aspects: list[str]
    reasoning_summary: str
    edit_strategy: str = NEW_TASK_MODIFY_EXISTING_SUBSECTION
    subsection_title: str = ""
    target_subsection_title: str = ""


@dataclass(frozen=True)
class NewTaskRewriteEvidence:
    """new_task 改写前准备好的证据上下文。

    Attributes:
        target (NewTaskTargetSection): 最终要编辑的目标章节。
        assets (SectionHistoricalAssets): 目标章节映射后的历史资产。
        assessment (NewTaskAssetAssessment): 历史资产充足性与编辑策略评估。
        merged_doc_infos (list): 最终用于写作的历史和增量文档信息。
        incremental_plan (Plan | None): 资料不足时构造的增量采集计划。
        incremental_doc_infos (list): 本轮增量采集得到的新文档信息。
    """

    target: NewTaskTargetSection
    assets: SectionHistoricalAssets
    assessment: NewTaskAssetAssessment
    merged_doc_infos: list
    incremental_plan: Plan | None
    incremental_doc_infos: list


@dataclass(frozen=True)
class NewTaskEditResult:
    """new_task 编辑策略应用后的报告区间结果。

    Attributes:
        new_report (str): 应用编辑后的完整报告。
        original_text (str): 被替换的原文；追加小节时为空字符串。
        original_start_offset (int): 原文替换区间起始偏移。
        original_end_offset (int): 原文替换区间结束偏移。
        original_text_clean (str): 用户选区清理 markup 后的文本。
        rewritten_text (str): 新增或重写后的 Markdown 片段。
        rewritten_start_offset (int): 新文本在新报告中的起始偏移。
        rewritten_end_offset (int): 新文本在新报告中的结束偏移。
        section_start_offset (int): 本次编辑所属章节起始偏移。
        section_end_offset (int): 本次编辑所属章节结束偏移。
        section_title (str): 本次编辑所属章节标题。
        new_subsection_title (str): 追加小节时生成的新标题；非追加策略为空字符串。
    """

    new_report: str
    original_text: str
    original_start_offset: int
    original_end_offset: int
    original_text_clean: str
    rewritten_text: str
    rewritten_start_offset: int
    rewritten_end_offset: int
    section_start_offset: int
    section_end_offset: int
    section_title: str
    new_subsection_title: str = ""


class NewTaskProcessor(UserFeedbackPromptInvoker):
    """处理 ``new_task`` 动作的章节级改写流程。"""

    # Markdown 标题解析规则：用于定位章节块、校验 LLM 输出标题层级。
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$", re.MULTILINE)

    # 标题归一化规则：用于容忍章节编号、空格和标点差异。
    _TITLE_PREFIX_RE = re.compile(
        r"^(?:第[一二三四五六七八九十百零两\d]+[章节篇部分卷]|[一二三四五六七八九十百零两\d]+[\.、])\s*"
    )
    _TITLE_PUNCT_TRANSLATION = str.maketrans("", "", " \t\r\n-_:：;；,.，。()（）[]【】<>《》/\\!?！？'\"`·")

    def __init__(self, llm_model_name: str):
        self.llm_model_name = llm_model_name

    @staticmethod
    def _raise_rewrite_error(message: str) -> None:
        """抛出统一的 ``new_task`` 改写异常。

        Args:
            message (str): 具体错误信息。

        Raises:
            CustomValueException: 统一包装后的改写异常。
        """
        raise CustomValueException(
            StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
            StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=message),
        )

    @staticmethod
    def _collect_doc_infos(history_plans) -> list:
        """从历史 plans 中聚合并去重 doc_infos。

        Args:
            history_plans (list): 历史章节规划列表，元素可为对象或字典。

        Returns:
            list: 去重后的历史文档信息列表。
        """
        doc_infos = []
        for plan in history_plans or []:
            steps = plan.steps if hasattr(plan, "steps") else plan.get("steps", [])
            for step in steps:
                retrieval_queries = (
                    step.retrieval_queries if hasattr(step, "retrieval_queries")
                    else step.get("retrieval_queries", [])
                )
                for query in retrieval_queries:
                    query_doc_infos = (
                        query.doc_infos if hasattr(query, "doc_infos")
                        else query.get("doc_infos", [])
                    )
                    doc_infos.extend(query_doc_infos)
        return NewTaskProcessor._deduplicate_doc_infos(doc_infos)

    @staticmethod
    def _deduplicate_doc_infos(doc_infos: list) -> list:
        """过滤异常文档后，复用报告侧统一 doc_info 去重逻辑。

        Args:
            doc_infos (list): 待去重的文档信息列表。

        Returns:
            list: 去重后的文档信息列表；结构异常的条目会被跳过。
        """
        valid_doc_infos = []
        malformed_count = 0
        for doc in doc_infos or []:
            if not isinstance(doc, dict) or not doc.get("title") or not doc.get("url"):
                malformed_count += 1
                continue
            valid_doc_infos.append(doc)
        if malformed_count:
            logger.warning(
                "[NewTaskProcessor] filtered malformed doc_infos while deduplicating. malformed_count=%s",
                malformed_count,
            )
        return deduplicate_doc_infos(valid_doc_infos)

    @staticmethod
    def _extract_major_section_number(title: str) -> str:
        """从大章节标题中提取数字编号。

        Args:
            title (str): 大章节标题，例如 ``1. 标题``。

        Returns:
            str: 数字编号；无法提取时返回空字符串。
        """
        match = re.match(r"^(?P<number>\d+)\.\s*", (title or "").strip())
        return match.group("number") if match else ""

    @staticmethod
    def _extract_subsection_index(title: str, major_number: str) -> int | None:
        """从小章节标题中提取 ``N.x`` 的小节序号。

        Args:
            title (str): 小章节标题。
            major_number (str): 所属大章节编号。

        Returns:
            int | None: 小节序号；无法提取时返回 ``None``。
        """
        if not major_number:
            return None
        match = re.match(rf"^{re.escape(major_number)}\.(?P<index>\d+)\b", (title or "").strip())
        return int(match.group("index")) if match else None

    @staticmethod
    def _strip_markup_from_report_range(report: str, start_offset: int, end_offset: int) -> str:
        """剥离指定报告区间内的引用/推理标记并返回区间文本。

        Args:
            report (str): 完整报告正文。
            start_offset (int): 待处理区间起始偏移。
            end_offset (int): 待处理区间结束偏移。

        Returns:
            str: 剥离 markup 后的区间文本。
        """
        stripped_report, _, _ = strip_markup_in_range(report, start_offset, end_offset)
        # strip 后目标区间右边界会左移；用删除长度修正原始 offset。
        delta = len(report) - len(stripped_report)
        return stripped_report[start_offset:end_offset - delta]

    @staticmethod
    def _strip_markup_from_target_section(
        report_content: str,
        target: NewTaskTargetSection,
    ) -> tuple[str, NewTaskTargetSection]:
        """剥离目标小章节的溯源标记并重建章节定位信息。

        Args:
            report_content (str): 当前完整报告正文。
            target (NewTaskTargetSection): 已根据用户选区定位出的目标章节。

        Returns:
            tuple[str, NewTaskTargetSection]: 剥离 markup 后的完整报告，以及基于新报告重建的目标小章节。
        """
        range_start = target.section_start_offset
        range_end = target.section_end_offset
        stripped_report, _, _ = strip_markup_in_range(report_content, range_start, range_end)
        if stripped_report == report_content:
            return report_content, target

        removed_length = len(report_content) - len(stripped_report)
        # 只剥离将被重写的小章节 markup，避免影响报告其他部分的引用和前端定位。
        rebuilt_section_end = range_end - removed_length
        rebuilt_section_text = stripped_report[range_start:rebuilt_section_end]
        has_major_range = (
            bool(target.major_section_text)
            and target.major_section_end_offset > target.major_section_start_offset
        )
        if has_major_range:
            rebuilt_major_end = target.major_section_end_offset - removed_length
            rebuilt_major_text = stripped_report[target.major_section_start_offset:rebuilt_major_end]
            rebuilt_major_clean_text = NewTaskProcessor._strip_markup_from_report_range(
                stripped_report,
                target.major_section_start_offset,
                rebuilt_major_end,
            )
        else:
            rebuilt_major_end = rebuilt_section_end
            rebuilt_major_text = rebuilt_section_text
            rebuilt_major_clean_text = NewTaskProcessor._strip_markup_from_report_range(
                stripped_report,
                range_start,
                rebuilt_section_end,
            )

        return stripped_report, NewTaskTargetSection(
            section_title=target.section_title,
            section_text=rebuilt_section_text,
            clean_section_text=NewTaskProcessor._strip_markup_from_report_range(
                stripped_report,
                range_start,
                rebuilt_section_end,
            ),
            clean_selected_text=target.clean_selected_text,
            section_start_offset=range_start,
            section_end_offset=rebuilt_section_end,
            major_section_title=target.major_section_title,
            major_section_text=rebuilt_major_text,
            clean_major_section_text=rebuilt_major_clean_text,
            major_section_start_offset=target.major_section_start_offset,
            major_section_end_offset=rebuilt_major_end,
            major_heading_level=target.major_heading_level,
        )

    @staticmethod
    def _find_subsection_block_in_major_section(target: NewTaskTargetSection, subsection_title: str) -> dict | None:
        """在目标大章节内查找指定小章节标题块。

        Args:
            target (NewTaskTargetSection): 当前 new_task 的目标上下文。
            subsection_title (str): 待查找的小章节标题。

        Returns:
            dict | None: 小章节标题块信息；未命中时返回 ``None``。
        """
        if not subsection_title or not target.major_section_text:
            return None
        headings = parse_markdown_headings(target.major_section_text)
        normalized_title = NewTaskProcessor._normalize_section_title(subsection_title)
        for index, heading in enumerate(headings):
            if heading["level"] != target.major_heading_level + 1:
                continue
            if (
                heading["title"] == subsection_title
                or NewTaskProcessor._normalize_section_title(heading["title"]) == normalized_title
            ):
                block_start = target.major_section_start_offset + heading["start"]
                block_end = target.major_section_start_offset + heading_block_end(
                    target.major_section_text,
                    headings,
                    index,
                )
                return {
                    **heading,
                    "block_start": block_start,
                    "block_end": block_end,
                }
        return None

    @staticmethod
    def _find_matched_section(target, current_outline):
        """按设计优先级为目标章节查找结构化映射。

        Args:
            target (NewTaskTargetSection): 目标 markdown 章节。
            current_outline: 当前结构化 outline。

        Returns:
            tuple: ``(matched_section, match_mode)``。
        """
        sections = list(getattr(current_outline, "sections", []) or [])
        if not sections:
            return None, "none"

        # 章节资产映射只接受标题精确/归一化匹配，避免顺序或正文相似度带来的误复用。
        for section in sections:
            if section.title == target.section_title:
                return section, "title_exact"

        normalized_target_title = NewTaskProcessor._normalize_section_title(target.section_title)
        for section in sections:
            if NewTaskProcessor._normalize_section_title(section.title) == normalized_target_title:
                return section, "title_normalized"

        return None, "none"

    @staticmethod
    def _normalize_section_title(title: str) -> str:
        """归一化章节标题，便于容忍编号、空格与标点差异。

        Args:
            title (str): 原始章节标题。

        Returns:
            str: 归一化后的标题文本。
        """
        stripped_title = (title or "").strip()
        normalized = NewTaskProcessor._TITLE_PREFIX_RE.sub("", stripped_title)
        if not normalized:
            normalized = stripped_title
        return normalized.casefold().translate(NewTaskProcessor._TITLE_PUNCT_TRANSLATION)

    @staticmethod
    def _validate_rewritten_section(target, rewritten_section: str) -> str:
        """校验整章重写结果的基础结构。

        Args:
            target (NewTaskTargetSection): 原始目标章节。
            rewritten_section (str): LLM 返回的重写章节文本。

        Returns:
            str: 去首尾空白后的合法章节文本。

        Raises:
            CustomValueException: 当输出为空、标题缺失或生成了更高层级标题时抛出。
        """
        normalized_output = (rewritten_section or "").strip()
        if not normalized_output:
            NewTaskProcessor._raise_rewrite_error("new_task rewrite returned empty output.")

        # 改写已有小节时必须保留原标题层级，避免 LLM 把内容扩写成更大的章节块。
        original_heading_match = NewTaskProcessor._HEADING_RE.match(target.clean_section_text.splitlines()[0].strip())
        expected_heading_level = len(original_heading_match.group(1)) if original_heading_match else None

        headings = []
        for line in normalized_output.splitlines():
            match = NewTaskProcessor._HEADING_RE.match(line.strip())
            if match:
                headings.append((len(match.group(1)), match.group(2).strip()))

        if expected_heading_level is not None and any(level < expected_heading_level for level, _ in headings):
            NewTaskProcessor._raise_rewrite_error("new_task rewrite contains unexpected higher-level headings.")

        if not headings or headings[0][1] != target.section_title:
            NewTaskProcessor._raise_rewrite_error(
                f"new_task rewrite missing original section title: {target.section_title}"
            )
        first_line_match = NewTaskProcessor._HEADING_RE.match(normalized_output.splitlines()[0].strip())
        if not first_line_match or first_line_match.group(2).strip() != target.section_title:
            NewTaskProcessor._raise_rewrite_error(
                f"new_task rewrite must start with original section title: {target.section_title}"
            )
        if expected_heading_level is not None and headings[0][0] != expected_heading_level:
            NewTaskProcessor._raise_rewrite_error(
                f"new_task rewrite changed original section heading level: {target.section_title}"
            )

        return normalized_output

    @staticmethod
    def _restore_original_section_separator(
        rewritten_section: str,
        original_section_text: str,
        trailing_content: str,
    ) -> str:
        """恢复整章改写后与后续内容之间的原始换行分隔。

        Args:
            rewritten_section (str): LLM 返回并已通过结构校验的改写章节文本。
            original_section_text (str): 原章节清洗后的完整文本，用于提取章节末尾换行。
            trailing_content (str): 原报告中位于目标章节之后的尾部内容。

        Returns:
            str: 已补回章节边界换行的改写章节文本。
        """
        if not trailing_content:
            return rewritten_section

        # 重写段末尾换行沿用原文，防止和下一章节标题粘连或额外空行漂移。
        match = re.search(r"(\n+)$", original_section_text)
        if match:
            normalized_section = rewritten_section.rstrip("\n")
            return f"{normalized_section}{match.group(1)}"
        return rewritten_section

    @staticmethod
    def _build_target_from_subsection_block(
        original_target: NewTaskTargetSection,
        report_content: str,
        block: dict,
    ) -> NewTaskTargetSection:
        """基于同一大章节内的小章节块构造改写目标。

        Args:
            original_target (NewTaskTargetSection): 用户选区解析出的原始目标上下文。
            report_content (str): 当前完整报告正文。
            block (dict): 小章节标题块信息。

        Returns:
            NewTaskTargetSection: 指向指定小章节的新目标对象。
        """
        section_start = block["block_start"]
        section_end = block["block_end"]
        return NewTaskTargetSection(
            section_title=block["title"],
            section_text=report_content[section_start:section_end],
            clean_section_text=NewTaskProcessor._strip_markup_from_report_range(
                report_content,
                section_start,
                section_end,
            ),
            clean_selected_text=original_target.clean_selected_text,
            section_start_offset=section_start,
            section_end_offset=section_end,
            major_section_title=original_target.major_section_title,
            major_section_text=original_target.major_section_text,
            clean_major_section_text=original_target.clean_major_section_text,
            major_section_start_offset=original_target.major_section_start_offset,
            major_section_end_offset=original_target.major_section_end_offset,
            major_heading_level=original_target.major_heading_level,
        )

    @staticmethod
    def _resolve_existing_subsection_target(
        target: NewTaskTargetSection,
        report_content: str,
        assessment: NewTaskAssetAssessment,
    ) -> NewTaskTargetSection:
        """解析本轮应修改的已有小章节。

        Args:
            target (NewTaskTargetSection): 用户选区解析出的目标上下文。
            report_content (str): 当前完整报告正文。
            assessment (NewTaskAssetAssessment): 资料评估与编辑策略结果。

        Returns:
            NewTaskTargetSection: 应被改写的小章节目标；未指定或未命中时回退到选区所在小章节。
        """
        target_title = assessment.target_subsection_title or assessment.subsection_title
        block = NewTaskProcessor._find_subsection_block_in_major_section(target, target_title)
        if block is None:
            return target
        return NewTaskProcessor._build_target_from_subsection_block(
            original_target=target,
            report_content=report_content,
            block=block,
        )

    @staticmethod
    def _is_same_target_section(left: NewTaskTargetSection, right: NewTaskTargetSection) -> bool:
        """判断两个目标章节是否指向同一段报告区间。

        Args:
            left (NewTaskTargetSection): 第一个目标章节。
            right (NewTaskTargetSection): 第二个目标章节。

        Returns:
            bool: 标题和原文区间一致时返回 ``True``。
        """
        return (
            left.section_title == right.section_title
            and left.section_start_offset == right.section_start_offset
            and left.section_end_offset == right.section_end_offset
        )

    @staticmethod
    def _is_english_language(language: str) -> bool:
        """判断当前语言是否为英文。

        Args:
            language (str): 当前报告语言标识。

        Returns:
            bool: 英文语言返回 ``True``，否则返回 ``False``。
        """
        return (language or "").lower().startswith("en")

    @staticmethod
    def _fallback_subsection_title(language: str) -> str:
        """按报告语言返回新增小节兜底标题。

        Args:
            language (str): 当前报告语言标识。

        Returns:
            str: 对应语言下的新增小节兜底标题。
        """
        return "Additional Analysis" if NewTaskProcessor._is_english_language(language) else "新增补充分析"

    @staticmethod
    def _build_next_subsection_title(
        target: NewTaskTargetSection,
        suggested_title: str,
        language: str = "zh-CN",
    ) -> str:
        """构造追加小章节的下一个 ``N.x`` 标题。

        Args:
            target (NewTaskTargetSection): 用户选区解析出的目标上下文。
            suggested_title (str): LLM 或用户指令给出的新小节主题。
            language (str): 当前报告语言标识。

        Returns:
            str: 带编号的新小章节标题。
        """
        major_number = NewTaskProcessor._extract_major_section_number(target.major_section_title)
        headings = parse_markdown_headings(target.major_section_text)
        # 只统计当前大章节下一层的小节编号，用于生成连续的 N.x 标题。
        subsection_indices = []
        for heading in headings:
            if heading["level"] != target.major_heading_level + 1:
                continue
            index = NewTaskProcessor._extract_subsection_index(heading["title"], major_number)
            if index is not None:
                subsection_indices.append(index)
        next_index = max(subsection_indices, default=0) + 1
        fallback_title = NewTaskProcessor._fallback_subsection_title(language)
        raw_title = (suggested_title or fallback_title).strip()
        explicit_title_match = re.search(
            r"(?:标题(?:为|是)|title\s+(?:as|is)|titled)\s*[“\"'](?P<title>[^”\"']+)[”\"']",
            raw_title,
            flags=re.IGNORECASE,
        )
        if explicit_title_match:
            raw_title = explicit_title_match.group("title").strip()
        # LLM/用户可能已给出编号，统一去掉后由系统按当前位置重新编号。
        raw_title = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", raw_title).strip() or fallback_title
        return f"{major_number}.{next_index} {raw_title}" if major_number else raw_title

    @staticmethod
    def _append_subsection_to_major_section(
        report_content: str,
        target: NewTaskTargetSection,
        subsection_text: str,
    ) -> str:
        """将新小章节追加到所属大章节末尾。

        Args:
            report_content (str): 当前完整报告正文。
            target (NewTaskTargetSection): 用户选区解析出的目标上下文。
            subsection_text (str): 已校验的新小章节 Markdown。

        Returns:
            str: 插入新小章节后的完整报告。
        """
        major_text = report_content[target.major_section_start_offset:target.major_section_end_offset]
        major_body = major_text.rstrip("\n")
        trailing_newlines = major_text[len(major_body):]
        separator_after_new_section = trailing_newlines if len(trailing_newlines) >= 2 else "\n\n"
        new_major_text = f"{major_body}\n\n{subsection_text.strip()}{separator_after_new_section}"
        return (
            report_content[: target.major_section_start_offset]
            + new_major_text
            + report_content[target.major_section_end_offset:]
        )

    @staticmethod
    def _validate_new_subsection(
        target: NewTaskTargetSection,
        subsection_text: str,
        subsection_title: str,
    ) -> str:
        """校验、截取并规范化新增小章节的 Markdown 结构。

        Args:
            target (NewTaskTargetSection): 用户选区解析出的目标上下文。
            subsection_text (str): LLM 生成的新小章节文本。
            subsection_title (str): 期望的新小章节标题。

        Returns:
            str: 清理首尾空白后的新小章节文本。

        Raises:
            CustomValueException: 当新增小章节缺少标题或标题层级非法时抛出。
        """
        normalized_output = (subsection_text or "").strip()
        if not normalized_output:
            NewTaskProcessor._raise_rewrite_error("new_task append subsection returned empty output.")

        expected_level = target.major_heading_level + 1
        expected_heading = f"{'#' * expected_level} {subsection_title}"
        expected_title = NewTaskProcessor._normalize_section_title(subsection_title)
        headings = parse_markdown_headings(normalized_output)
        matched_heading_index = None
        for index, heading in enumerate(headings):
            normalized_title = NewTaskProcessor._normalize_section_title(heading["title"])
            if normalized_title == expected_title:
                matched_heading_index = index
                break
        if matched_heading_index is not None:
            # LLM 有时返回整段大章节；命中期望小节后只截取该小节块。
            matched_heading = headings[matched_heading_index]
            block_end = heading_block_end(normalized_output, headings, matched_heading_index)
            normalized_output = normalized_output[matched_heading["start"]:block_end].strip()

        lines = normalized_output.splitlines()
        first_heading_index = None
        for index, line in enumerate(lines):
            if NewTaskProcessor._HEADING_RE.match(line.strip()):
                first_heading_index = index
                break
        if first_heading_index is None:
            lines = [expected_heading, *lines]
        else:
            lines[first_heading_index] = expected_heading

        normalized_output = "\n".join(lines).strip()
        invalid_headings = []
        for line in normalized_output.splitlines()[1:]:
            match = NewTaskProcessor._HEADING_RE.match(line.strip())
            if match and len(match.group(1)) <= expected_level:
                invalid_headings.append((len(match.group(1)), match.group(2).strip()))
        if invalid_headings:
            NewTaskProcessor._raise_rewrite_error("new_task append subsection contains unexpected major headings.")
        return normalized_output

    @staticmethod
    def _load_current_outline():
        """读取当前 session 中的结构化大纲状态。

        Returns:
            当前结构化大纲；session 不存在时返回 ``None``。
        """
        session = resolve_session_collector()
        return (
            session.get_global_state("search_context.current_outline")
            if session is not None
            else None
        )

    async def _prepare_rewrite_evidence(
        self,
        report_content: str,
        target: NewTaskTargetSection,
        feedback: dict,
        current_outline,
        language: str,
    ) -> NewTaskRewriteEvidence:
        """准备 new_task 改写所需的历史或增量证据。

        Args:
            report_content (str): 当前完整报告正文。
            target (NewTaskTargetSection): 用户选区解析出的目标章节上下文。
            feedback (dict): 用户反馈信息。
            current_outline: 当前结构化大纲。
            language (str): 当前报告语言。

        Returns:
            NewTaskRewriteEvidence: 资产映射、资料评估和最终证据文档。

        Raises:
            CustomValueException: 历史和增量采集都没有可用证据时抛出。
        """
        # 优先复用同章节历史研究资产；只接受标题精确/归一化匹配，避免误用相邻章节资料。
        assets = self.collect_section_assets(
            target=target,
            current_outline=current_outline,
        )
        logger.info(
            "[NewTaskProcessor] historical assets prepared. "
            "match_mode=%s section_id=%s section_title=%s historical_doc_count=%s historical_plan_count=%s",
            assets.match_mode,
            assets.section_id,
            assets.section_title,
            len(assets.historical_doc_infos),
            len(assets.historical_plans),
        )
        # 由 LLM 判断历史资料是否足够，并决定是改写已有小节还是追加新小节。
        assessment = await self.assess_section_assets(
            assets=assets,
            feedback=feedback,
            language=language,
        )
        logger.info(
            "[NewTaskProcessor] asset assessment completed. "
            "is_sufficient=%s edit_strategy=%s relevant_doc_count=%s missing_aspect_count=%s "
            "subsection_title=%s target_subsection_title=%s",
            assessment.is_sufficient,
            assessment.edit_strategy,
            len(assessment.relevant_doc_infos),
            len(assessment.missing_aspects),
            assessment.subsection_title,
            assessment.target_subsection_title,
        )

        rewrite_target = target
        if assessment.edit_strategy == NEW_TASK_MODIFY_EXISTING_SUBSECTION:
            rewrite_target = self._resolve_existing_subsection_target(
                target=target,
                report_content=report_content,
                assessment=assessment,
            )
            if not self._is_same_target_section(rewrite_target, target):
                assets = self.collect_section_assets(
                    target=rewrite_target,
                    current_outline=current_outline,
                )
                logger.info(
                    "[NewTaskProcessor] retargeted historical assets prepared. "
                    "match_mode=%s section_id=%s section_title=%s historical_doc_count=%s historical_plan_count=%s",
                    assets.match_mode,
                    assets.section_id,
                    assets.section_title,
                    len(assets.historical_doc_infos),
                    len(assets.historical_plans),
                )
                retargeted_assessment = await self.assess_section_assets(
                    assets=assets,
                    feedback=feedback,
                    language=language,
                )
                # 第二次评估只用于刷新实际改写小节的证据充足性和相关文档；
                # 编辑策略和目标小节沿用第一次评估结果，避免跨小节重定向反复漂移。
                assessment = replace(
                    retargeted_assessment,
                    edit_strategy=NEW_TASK_MODIFY_EXISTING_SUBSECTION,
                    target_subsection_title=rewrite_target.section_title,
                    subsection_title="",
                )
                logger.info(
                    "[NewTaskProcessor] retargeted asset assessment completed. "
                    "is_sufficient=%s relevant_doc_count=%s missing_aspect_count=%s",
                    assessment.is_sufficient,
                    len(assessment.relevant_doc_infos),
                    len(assessment.missing_aspects),
                )

        merged_doc_infos = list(assessment.relevant_doc_infos)
        incremental_plan = None
        incremental_doc_infos = []

        if not assessment.is_sufficient:
            logger.info(
                "[NewTaskProcessor] incremental collection started. missing_aspect_count=%s",
                len(assessment.missing_aspects),
            )
            # 历史资料不足时才触发增量采集；采集结果和可复用历史文档一起作为写作证据。
            incremental_plan = await self.build_incremental_plan(
                assets=assets,
                feedback=feedback,
                language=language,
                assessment=assessment,
            )
            collection = await self.run_incremental_collection(
                plan=incremental_plan,
                language=language,
            )
            incremental_doc_infos = collection.get("doc_infos", [])
            merged_doc_infos = self._deduplicate_doc_infos([*merged_doc_infos, *incremental_doc_infos])
            logger.info(
                "[NewTaskProcessor] incremental collection completed. "
                "new_doc_count=%s merged_doc_count=%s plan_step_count=%s",
                len(incremental_doc_infos),
                len(merged_doc_infos),
                len(getattr(incremental_plan, "steps", []) or []),
            )
        else:
            logger.info(
                "[NewTaskProcessor] incremental collection skipped. "
                "reason=sufficient_historical_assets relevant_doc_count=%s",
                len(merged_doc_infos),
            )

        if not merged_doc_infos:
            self._raise_rewrite_error(
                "No evidence available for new_task rewrite after checking historical and incremental assets."
            )

        return NewTaskRewriteEvidence(
            target=rewrite_target,
            assets=assets,
            assessment=assessment,
            merged_doc_infos=merged_doc_infos,
            incremental_plan=incremental_plan,
            incremental_doc_infos=incremental_doc_infos,
        )

    async def _apply_append_new_subsection_strategy(
        self,
        report_content: str,
        target: NewTaskTargetSection,
        feedback: dict,
        evidence: NewTaskRewriteEvidence,
        language: str,
    ) -> NewTaskEditResult:
        """执行新增小章节策略。

        Args:
            report_content (str): 当前完整报告正文。
            target (NewTaskTargetSection): 用户选区解析出的目标章节上下文。
            feedback (dict): 用户反馈信息。
            evidence (NewTaskRewriteEvidence): 已准备好的证据和评估结果。
            language (str): 当前报告语言。

        Returns:
            NewTaskEditResult: 追加新小节后的报告文本和区间信息。
        """
        assessment = evidence.assessment
        # 追加模式不清理原大章节 markup，避免破坏既有引用；只把新小节插到大章节末尾。
        new_subsection_title = self._build_next_subsection_title(
            target=target,
            suggested_title=assessment.subsection_title or feedback.get("user_instruction", ""),
            language=language,
        )
        logger.info(
            "[NewTaskProcessor] edit strategy selected. "
            "edit_strategy=append_new_subsection major_section_title=%s new_subsection_title=%s "
            "insert_offset=%s evidence_doc_count=%s",
            target.major_section_title,
            new_subsection_title,
            target.major_section_end_offset,
            len(evidence.merged_doc_infos),
        )
        rewritten_section = await self.generate_new_subsection_with_assets(
            target=target,
            feedback=feedback,
            doc_infos=evidence.merged_doc_infos,
            language=language,
            subsection_title=new_subsection_title,
        )
        new_report = self._append_subsection_to_major_section(
            report_content=report_content,
            target=target,
            subsection_text=rewritten_section,
        )
        original_start_offset = target.major_section_end_offset
        rewritten_suffix = report_content[target.major_section_end_offset:]
        rewritten_end_offset = len(new_report) - len(rewritten_suffix)
        rewritten_start_offset = original_start_offset
        rewritten_section = new_report[rewritten_start_offset:rewritten_end_offset]
        return NewTaskEditResult(
            new_report=new_report,
            original_text="",
            original_start_offset=original_start_offset,
            original_end_offset=target.major_section_end_offset,
            original_text_clean=target.clean_selected_text,
            rewritten_text=rewritten_section,
            rewritten_start_offset=rewritten_start_offset,
            rewritten_end_offset=rewritten_end_offset,
            section_start_offset=target.major_section_start_offset,
            section_end_offset=target.major_section_end_offset,
            section_title=target.major_section_title,
            new_subsection_title=new_subsection_title,
        )

    async def _apply_modify_existing_subsection_strategy(
        self,
        report_content: str,
        target: NewTaskTargetSection,
        feedback: dict,
        evidence: NewTaskRewriteEvidence,
        language: str,
    ) -> NewTaskEditResult:
        """执行改写已有小章节策略。

        Args:
            report_content (str): 当前完整报告正文。
            target (NewTaskTargetSection): 用户选区解析出的目标章节上下文。
            feedback (dict): 用户反馈信息。
            evidence (NewTaskRewriteEvidence): 已准备好的证据和评估结果。
            language (str): 当前报告语言。

        Returns:
            NewTaskEditResult: 改写已有小节后的报告文本和区间信息。
        """
        assessment = evidence.assessment

        # 修改已有小节时，证据准备阶段已经把目标切到实际应改写的小节。
        target = evidence.target
        logger.info(
            "[NewTaskProcessor] edit strategy selected. "
            "edit_strategy=modify_existing_subsection section_title=%s section_start_offset=%s "
            "section_end_offset=%s evidence_doc_count=%s",
            target.section_title,
            target.section_start_offset,
            target.section_end_offset,
            len(evidence.merged_doc_infos),
        )
        original_replaced_target = target
        # 只有即将被替换的小节需要去 markup，追加新小节时必须保留原大章节引用。
        report_content, target = self._strip_markup_from_target_section(
            report_content=report_content,
            target=target,
        )
        rewritten_section = await self.rewrite_section_with_assets(
            target=target,
            feedback=feedback,
            doc_infos=evidence.merged_doc_infos,
            language=language,
        )
        trailing_content = report_content[target.section_end_offset:]
        rewritten_section = self._restore_original_section_separator(
            rewritten_section=rewritten_section,
            original_section_text=target.clean_section_text,
            trailing_content=trailing_content,
        )
        if rewritten_section.endswith("\n") and trailing_content.startswith("\n"):
            trailing_content = trailing_content[1:]
        new_report = (
            report_content[: target.section_start_offset]
            + rewritten_section
            + trailing_content
        )
        return NewTaskEditResult(
            new_report=new_report,
            original_text=original_replaced_target.section_text,
            original_start_offset=original_replaced_target.section_start_offset,
            original_end_offset=original_replaced_target.section_end_offset,
            original_text_clean=target.clean_selected_text,
            rewritten_text=rewritten_section,
            rewritten_start_offset=target.section_start_offset,
            rewritten_end_offset=target.section_start_offset + len(rewritten_section),
            section_start_offset=target.section_start_offset,
            section_end_offset=target.section_end_offset,
            section_title=target.section_title,
        )

    async def _apply_new_task_edit_strategy(
        self,
        report_content: str,
        target: NewTaskTargetSection,
        feedback: dict,
        evidence: NewTaskRewriteEvidence,
        language: str,
    ) -> NewTaskEditResult:
        """按评估结果分发 new_task 编辑策略。

        Args:
            report_content (str): 当前完整报告正文。
            target (NewTaskTargetSection): 用户选区解析出的目标章节上下文。
            feedback (dict): 用户反馈信息。
            evidence (NewTaskRewriteEvidence): 已准备好的证据和评估结果。
            language (str): 当前报告语言。

        Returns:
            NewTaskEditResult: 应用编辑策略后的报告文本和区间信息。
        """
        if evidence.assessment.edit_strategy == NEW_TASK_APPEND_NEW_SUBSECTION:
            return await self._apply_append_new_subsection_strategy(
                report_content=report_content,
                target=target,
                feedback=feedback,
                evidence=evidence,
                language=language,
            )
        return await self._apply_modify_existing_subsection_strategy(
            report_content=report_content,
            target=target,
            feedback=feedback,
            evidence=evidence,
            language=language,
        )

    @staticmethod
    def _build_new_task_result(
        edit_result: NewTaskEditResult,
        evidence: NewTaskRewriteEvidence,
    ) -> dict:
        """组装 new_task 对外返回结果。

        Args:
            edit_result (NewTaskEditResult): 编辑策略执行后的报告区间结果。
            evidence (NewTaskRewriteEvidence): 本轮使用的证据和评估上下文。

        Returns:
            dict: new_task 改写后的报告、原文区间、证据文档和增量研究信息。
        """
        # 返回的 offset 同时服务前端替换区间和历史同步，必须对应最终 new_report 中的文本边界。
        return {
            "new_report": edit_result.new_report,
            "original_text": edit_result.original_text,
            "original_start_offset": edit_result.original_start_offset,
            "original_end_offset": edit_result.original_end_offset,
            "original_text_clean": edit_result.original_text_clean,
            "rewritten_text": edit_result.rewritten_text,
            "rewritten_start_offset": edit_result.rewritten_start_offset,
            "rewritten_end_offset": edit_result.rewritten_end_offset,
            "section_start_offset": edit_result.section_start_offset,
            "section_end_offset": edit_result.section_end_offset,
            "section_title": edit_result.section_title,
            "matched_section_id": evidence.assets.section_id,
            "match_mode": evidence.assets.match_mode,
            "assessment_summary": evidence.assessment.reasoning_summary,
            "used_historical_doc_count": len(evidence.assessment.relevant_doc_infos),
            "used_new_doc_count": len(evidence.incremental_doc_infos),
            "incremental_plan": evidence.incremental_plan,
            "incremental_doc_infos": evidence.incremental_doc_infos,
            "missing_aspects": evidence.assessment.missing_aspects,
            "edit_strategy": evidence.assessment.edit_strategy,
            "new_subsection_title": edit_result.new_subsection_title,
        }

    async def run_new_task(
        self,
        feedback: dict,
        final_result: dict,
        language: str,
    ) -> dict:
        """执行 ``new_task`` 章节级增量研究与重写流程。

        Args:
            feedback (dict): 用户反馈信息，包含选区、指令和动作类型。
            final_result (dict): 当前报告结果，包含待改写的报告正文。
            language (str): 当前报告语言。

        Returns:
            dict: new_task 改写后的报告、原文区间、证据文档和增量研究信息。
        """
        report_content = final_result.get("response_content", "") or ""
        # 先把用户选区落到 Markdown 小节和可选的编号大章节，后续所有 offset 都基于这个定位。
        target = self.resolve_target_section(report_content=report_content, feedback=feedback)
        logger.info(
            "[NewTaskProcessor] run_new_task started. "
            "section_title=%s major_section_title=%s selected_start_offset=%s selected_end_offset=%s "
            "section_start_offset=%s section_end_offset=%s",
            target.section_title,
            target.major_section_title,
            feedback.get("start_offset"),
            feedback.get("end_offset"),
            target.section_start_offset,
            target.section_end_offset,
        )
        current_outline = self._load_current_outline()
        evidence = await self._prepare_rewrite_evidence(
            report_content=report_content,
            target=target,
            feedback=feedback,
            current_outline=current_outline,
            language=language,
        )
        edit_result = await self._apply_new_task_edit_strategy(
            report_content=report_content,
            target=target,
            feedback=feedback,
            evidence=evidence,
            language=language,
        )
        logger.info(
            "[NewTaskProcessor] run_new_task completed. "
            "edit_strategy=%s used_historical_doc_count=%s used_new_doc_count=%s "
            "rewritten_start_offset=%s rewritten_end_offset=%s",
            evidence.assessment.edit_strategy,
            len(evidence.assessment.relevant_doc_infos),
            len(evidence.incremental_doc_infos),
            edit_result.rewritten_start_offset,
            edit_result.rewritten_end_offset,
        )
        return self._build_new_task_result(edit_result=edit_result, evidence=evidence)

    def resolve_target_section(self, report_content: str, feedback: dict) -> NewTaskTargetSection:
        """根据用户选区定位目标小章节及所属编号大章节。

        Args:
            report_content (str): 当前完整报告正文。
            feedback (dict): 用户反馈信息，包含选区偏移和选中文本。

        Returns:
            NewTaskTargetSection: 选区所在小章节、所属大章节及清洗后的文本。
        """
        located = locate_section(report_content, feedback["start_offset"], feedback["end_offset"])
        major_block = locate_enclosing_numbered_major_block(
            report=report_content,
            start_offset=feedback["start_offset"],
            end_offset=feedback["end_offset"],
        )
        clean_section_text = self._strip_markup_from_report_range(
            report_content,
            located.section_start_offset,
            located.section_end_offset,
        )
        # section、selected、major 三类文本范围不同，分别清理可避免 strip 后 offset 互相污染。
        clean_selected_text = self._strip_markup_from_report_range(
            report_content,
            feedback["start_offset"],
            feedback["end_offset"],
        )
        section_title = located.section_heading.lstrip("#").strip()
        if major_block:
            major_start = major_block.block_start
            major_end = major_block.block_end
            major_title = major_block.title
            major_text = report_content[major_start:major_end]
            clean_major_text = self._strip_markup_from_report_range(report_content, major_start, major_end)
            major_level = major_block.level
        else:
            major_start = located.section_start_offset
            major_end = located.section_end_offset
            major_title = section_title
            major_text = located.section_text
            clean_major_text = clean_section_text
            major_level = len(located.section_heading) - len(located.section_heading.lstrip("#"))
        return NewTaskTargetSection(
            section_title=section_title,
            section_text=located.section_text,
            clean_section_text=clean_section_text,
            clean_selected_text=clean_selected_text,
            section_start_offset=located.section_start_offset,
            section_end_offset=located.section_end_offset,
            major_section_title=major_title,
            major_section_text=major_text,
            clean_major_section_text=clean_major_text,
            major_section_start_offset=major_start,
            major_section_end_offset=major_end,
            major_heading_level=major_level,
        )

    def collect_section_assets(self, target, current_outline) -> SectionHistoricalAssets:
        """按章节标题映射 section，并汇总可复用历史资产。

        Args:
            target (NewTaskTargetSection): 用户选区解析出的目标章节上下文。
            current_outline: 当前结构化大纲，通常包含 sections 和历史 plans。

        Returns:
            SectionHistoricalAssets: 目标章节匹配结果及可复用的历史规划和文档。
        """
        matched_section, match_mode = self._find_matched_section(
            target=target,
            current_outline=current_outline,
        )

        historical_plans = (getattr(matched_section, "plans", []) or []) if matched_section else []
        historical_doc_infos = self._collect_doc_infos(historical_plans)

        return SectionHistoricalAssets(
            section_id=matched_section.id if matched_section else None,
            match_mode=match_mode,
            section_title=target.section_title,
            current_section_text=target.clean_major_section_text or target.clean_section_text,
            historical_plans=historical_plans,
            historical_doc_infos=historical_doc_infos,
        )

    @staticmethod
    def _normalize_edit_strategy(raw_strategy: str | None) -> str:
        """归一化 new_task 的编辑策略。

        Args:
            raw_strategy (str | None): LLM 返回的编辑策略。

        Returns:
            str: 归一化后的编辑策略。
        """
        if raw_strategy in {NEW_TASK_MODIFY_EXISTING_SUBSECTION, NEW_TASK_APPEND_NEW_SUBSECTION}:
            return raw_strategy
        return NEW_TASK_MODIFY_EXISTING_SUBSECTION

    async def assess_section_assets(
        self,
        assets,
        feedback: dict,
        language: str,
    ) -> NewTaskAssetAssessment:
        """评估历史资产是否足够支撑本轮新增任务。

        Args:
            assets (SectionHistoricalAssets): 目标章节映射后的历史资产视图。
            feedback (dict): 用户反馈信息，包含选区文本和改写指令。
            language (str): 当前报告语言。

        Returns:
            NewTaskAssetAssessment: 历史资料充足性、可复用文档和编辑策略评估结果。
        """
        response = await self._invoke_prompt(
            "new_task_assessment",
            {
                "language": language,
                "section_title": assets.section_title,
                "clean_section_text": assets.current_section_text,
                "selected_text": feedback.get("selected_text", ""),
                "user_instruction": feedback.get("user_instruction", ""),
                # 中间过渡态：new_task_assessment 仍使用旧 doc_infos prompt 协议。
                # 后续该 prompt 迁移到 evidence schema 后，需要删除该转换。
                "historical_doc_infos": build_legacy_doc_infos_view(assets.historical_doc_infos),
                "supported_edit_strategies": [
                    NEW_TASK_MODIFY_EXISTING_SUBSECTION,
                    NEW_TASK_APPEND_NEW_SUBSECTION,
                ],
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_NEW_TASK_ASSESSMENT.value,
        )

        try:
            data = json.loads(response)
        except (TypeError, json.JSONDecodeError) as error:
            logger.warning(
                "[NewTaskProcessor] assess_section_assets failed to parse LLM JSON; "
                "fallback to insufficient assets. response_type=%s error=%s",
                type(response).__name__,
                error,
            )
            data = {
                "relevant_doc_indices": [],
                "is_sufficient": False,
                "missing_aspects": [feedback.get("user_instruction", "")] if feedback.get("user_instruction") else [],
                "reasoning_summary": "无法稳定解析评估结果，已降级为资料不足。",
            }

        edit_strategy = self._normalize_edit_strategy(data.get("edit_strategy"))
        indices = data.get("relevant_doc_indices", [])
        invalid_indices = []
        relevant_doc_infos = []
        if not isinstance(indices, list):
            invalid_indices.append(indices)
            indices = []
        for index in indices:
            # LLM 返回的是面向 prompt 展示的一基索引；bool 是 int 子类，需要显式排除。
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 1 <= index <= len(assets.historical_doc_infos)
            ):
                invalid_indices.append(index)
                continue
            relevant_doc_infos.append(assets.historical_doc_infos[index - 1])
        if invalid_indices:
            logger.warning(
                "[NewTaskProcessor] assess_section_assets filtered invalid relevant_doc_indices. "
                "invalid_indices=%s total_docs=%s",
                invalid_indices,
                len(assets.historical_doc_infos),
            )

        raw_is_sufficient = data.get("is_sufficient", False)
        if isinstance(raw_is_sufficient, bool):
            is_sufficient = raw_is_sufficient
        else:
            is_sufficient = False
            logger.warning(
                "[NewTaskProcessor] assess_section_assets received invalid is_sufficient type. "
                "value=%s value_type=%s",
                raw_is_sufficient,
                type(raw_is_sufficient).__name__,
            )
        if is_sufficient and not relevant_doc_infos:
            is_sufficient = False
            logger.warning(
                "[NewTaskProcessor] downgraded sufficient assessment without valid relevant docs. "
                "total_docs=%s",
                len(assets.historical_doc_infos),
            )

        return NewTaskAssetAssessment(
            relevant_doc_infos=relevant_doc_infos,
            is_sufficient=is_sufficient,
            missing_aspects=data.get("missing_aspects", []),
            reasoning_summary=data.get("reasoning_summary", ""),
            edit_strategy=edit_strategy,
            subsection_title=data.get("subsection_title", "") or data.get("new_subsection_title", ""),
            target_subsection_title=data.get("target_subsection_title", "") or data.get("subsection_title", ""),
        )

    async def build_incremental_plan(
        self,
        assets,
        feedback: dict,
        language: str,
        assessment: NewTaskAssetAssessment | None = None,
    ) -> Plan:
        """在资料不足时构造增量收集计划。

        Args:
            assets: 目标章节对应的历史资产视图。
            feedback (dict): 用户反馈信息。
            language (str): 当前报告语言标识。
            assessment (NewTaskAssetAssessment | None): 已有评估结果；为空时会重新评估。

        Returns:
            Plan: 用于增量信息采集的计划。
        """
        assessment = assessment or await self.assess_section_assets(
            assets=assets,
            feedback=feedback,
            language=language,
        )
        separator = "; " if self._is_english_language(language) else "；"
        missing_summary = separator.join(assessment.missing_aspects) or feedback.get("user_instruction", "")
        if self._is_english_language(language):
            description = (
                f"User request: {feedback.get('user_instruction', '')}\n"
                f"Section title: {assets.section_title}\n"
                f"Missing information: {missing_summary}"
            )
        else:
            description = (
                f"用户要求：{feedback.get('user_instruction', '')}\n"
                f"章节标题：{assets.section_title}\n"
                f"待补充信息：{missing_summary}"
            )
        return Plan(
            id="",
            language=language,
            title="NEW_TASK incremental research",
            thought="Collect missing evidence required for the requested chapter update.",
            is_research_completed=False,
            steps=[
                Step(
                    type=StepType.INFO_COLLECTING,
                    title="Collect missing evidence",
                    description=description,
                )
            ],
        )

    async def run_incremental_collection(self, plan: Plan, language: str) -> dict:
        """执行增量信息采集计划并返回摘要与文档信息。

        Args:
            plan (Plan): 待执行的增量信息采集计划。
            language (str): 当前报告语言。

        Returns:
            dict: 增量采集摘要和新增文档信息。

        Raises:
            CustomValueException: 当前 session 不可用时抛出。
        """
        session = resolve_session_collector()
        context = resolve_model_context_collector()
        if session is None:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(
                    e="NEW_TASK requires session.",
                ),
            )

        service = CollectorExecutionService()
        result = await service.run_plan(
            plan=plan,
            run_config=CollectorRunPlanConfig(
                language=language,
                section_idx="new_task",
                initial_search_query_count=session.get_global_state(
                    "config.info_collector_initial_search_query_count"
                ),
                max_research_loops=session.get_global_state(
                    "config.info_collector_max_research_loops"
                ),
                max_react_recursion_limit=session.get_global_state(
                    "config.info_collector_max_react_recursion_limit"
                ),
            ),
            session=session,
            context=context,
        )
        return {
            "info_summary": result.info_summary or "",
            "doc_infos": result.doc_infos or [],
        }

    async def rewrite_section_with_assets(
        self,
        target,
        feedback: dict,
        doc_infos: list,
        language: str,
    ) -> str:
        """基于历史/增量资产重写目标章节。

        Args:
            target (NewTaskTargetSection): 待改写的目标章节上下文。
            feedback (dict): 用户反馈信息。
            doc_infos (list): 可用于章节改写的资料列表。
            language (str): 当前报告语言。

        Returns:
            str: 已通过结构校验的重写章节 Markdown。
        """
        response = await self._invoke_prompt(
            "new_task_rewrite_section",
            {
                "language": language,
                "edit_strategy": NEW_TASK_MODIFY_EXISTING_SUBSECTION,
                "major_section_title": target.major_section_title,
                "major_section_text": target.clean_major_section_text,
                "section_title": target.section_title,
                "clean_section_text": target.clean_section_text,
                "clean_selected_text": target.clean_selected_text,
                "user_instruction": feedback.get("user_instruction", ""),
                # 中间过渡态：new_task_rewrite_section 仍使用旧 doc_infos prompt 协议。
                # 后续该 prompt 迁移到 evidence schema 后，需要删除该转换。
                "doc_infos": build_legacy_doc_infos_view(doc_infos),
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_NEW_TASK_REWRITE_SECTION.value,
        )
        return self._validate_rewritten_section(target=target, rewritten_section=response)

    async def generate_new_subsection_with_assets(
        self,
        target: NewTaskTargetSection,
        feedback: dict,
        doc_infos: list,
        language: str,
        subsection_title: str,
    ) -> str:
        """基于历史/增量资产生成新的小章节。

        Args:
            target (NewTaskTargetSection): 用户选区所属大章节上下文。
            feedback (dict): 用户反馈信息。
            doc_infos (list): 可用于新增小节写作的资料列表。
            language (str): 当前报告语言。
            subsection_title (str): 带编号的新小章节标题。

        Returns:
            str: 已通过结构校验的新小章节 Markdown。
        """
        response = await self._invoke_prompt(
            "new_task_rewrite_section",
            {
                "language": language,
                "edit_strategy": NEW_TASK_APPEND_NEW_SUBSECTION,
                "major_section_title": target.major_section_title,
                "major_section_text": target.clean_major_section_text,
                "section_title": subsection_title,
                "clean_section_text": target.clean_major_section_text,
                "selected_subsection_title": target.section_title,
                "clean_selected_text": target.clean_selected_text,
                "new_subsection_title": subsection_title,
                "user_instruction": feedback.get("user_instruction", ""),
                # 中间过渡态：new_task_rewrite_section 仍使用旧 doc_infos prompt 协议。
                # 后续该 prompt 迁移到 evidence schema 后，需要删除该转换。
                "doc_infos": build_legacy_doc_infos_view(doc_infos),
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_NEW_TASK_REWRITE_SECTION.value,
        )
        return self._validate_new_subsection(
            target=target,
            subsection_text=response,
            subsection_title=subsection_title,
        )
