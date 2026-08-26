"""Brief 章节与专业版 Mermaid 图表流水线之间的算法适配。"""

import asyncio
import logging
from typing import Any

from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefChapter,
    BriefCollectionResult,
    BriefOutline,
    BriefSection,
)
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)


def _brief_visualization_outline(section: BriefSection) -> str:
    """将 Brief 章节合同映射为专业版 Mermaid 流水线需要的章节上下文。"""
    return "\n".join(
        [f"{section.id} {section.title}", section.goal]
        + [step.requirement for step in section.research_steps]
    )


def _brief_visualization_docs(collection: BriefCollectionResult, section_id: str) -> list[dict[str, Any]]:
    """将 Brief 已评估的章节证据映射为专业版图表代理的输入。"""
    evidence = collection.section_evidence.get(section_id)
    if evidence is None:
        return []
    registry_by_source = {item.source_id: item for item in collection.citation_registry}
    docs: list[dict[str, Any]] = []
    for selected in sorted(evidence.selected_docs, key=lambda item: (item.evaluation_rank, item.source_id)):
        record = registry_by_source.get(selected.source_id)
        if record is None:
            continue
        docs.append(
            {
                "title": record.title,
                "url": record.url,
                "original_content": record.original_content,
                "index": record.index,
                # Brief 只保留了已评估的证据，没有专业版分类阶段的 data_density；
                # 专业版图表选择器读取顶层字段，后续提取、可追溯性与合规校验
                # 决定其是否真正可视化。
                "data_density": 9.0,
            }
        )
    return docs


async def generate_brief_mermaid_visualizations(
    *,
    llm_model_name: str,
    outline: BriefOutline,
    collection: BriefCollectionResult,
    chapters: list[BriefChapter],
    language: str,
) -> list[BriefChapter]:
    """复用专业版受控 Mermaid 图表代理，为已完成的 Brief 章节插图。

    正文写作与图表生成严格分两次代理调用：本函数只消费正文和已评估证据，
    不重新写作章节；单个章节的图表失败只保留原正文，不中断整份 Brief。
    """
    reporter = Reporter(llm_model_name)
    section_by_id = {section.id: section for section in outline.sections}
    max_retries = 1

    async def _visualize_chapter(chapter: BriefChapter) -> BriefChapter:
        section = section_by_id.get(chapter.section_id)
        if section is None:
            return chapter
        current_inputs = {
            "section_idx": chapter.section_id,
            "section_task": section.title,
            "sub_section_outline": _brief_visualization_outline(section),
            "sub_report_content": chapter.raw_markdown,
            "classified_content": _brief_visualization_docs(collection, section.id),
            "language": language,
            "max_generate_retry_num": max_retries,
            "visualization_enable": True,
        }
        try:
            generation = await reporter.generate_content_for_visualization(current_inputs)
            current_inputs["visualization_result"] = generation.get("visualization_content", [])
            insertion = await reporter.insert_visualization(current_inputs)
            if insertion.get("rs_success", False):
                return chapter.model_copy(update={"raw_markdown": insertion.get("result", chapter.raw_markdown)})
        except Exception as exc:
            logger.warning(
                "[BriefMermaidGenerator] Chart generation failed; section_id=%s error=%s. Keep original chapter.",
                section.id,
                "<detail masked>" if LogManager.is_sensitive() else exc,
                exc_info=not LogManager.is_sensitive(),
            )
        return chapter

    return list(await asyncio.gather(*(_visualize_chapter(chapter) for chapter in chapters)))
