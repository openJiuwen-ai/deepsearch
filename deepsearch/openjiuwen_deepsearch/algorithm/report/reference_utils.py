# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reference management: deduplication, renumbering, and citation replacement."""

import logging
import re

from openjiuwen_deepsearch.common.common_constants import CHINESE
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

_REF_LINE_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")
_CITATION_RE = re.compile(r"\[citation:(\d+)\]")


def _deduplicate_and_renumber_refs(
    ref_blocks: list[str],
) -> tuple[str, dict[str, int]]:
    """去重引用并重编号，按段落返回 ``{段落号-原编号: 新编号}``。

    ``ref_blocks`` 必须按正文段落顺序给出每个段落的引用块，段落号直接取
    1-based 下标。调用方本就是这个顺序，不需要（也不应该）再从文本里用
    "空行/``[1]``"反推段落边界：那种启发式在引用块内出现空行、或某段引用
    不是从 ``[1]`` 起编号时会整体漂移，导致 ``ref_map`` 的键与消费端按
    ``f"{i + 1}-{index}"`` 拼出的键对不上，整段引用都不会被重编号。
    """
    seen: dict[str, int] = {}
    renumbered: list[str] = []
    mapping: dict[str, int] = {}
    next_index = 1

    for paragraph_no, block in enumerate(ref_blocks, start=1):
        if not block:
            continue
        for line in block.splitlines():
            match = _REF_LINE_RE.match(line.strip())
            if match is None:
                continue
            content = match.group(2).strip()
            if not content:
                continue
            if content not in seen:
                seen[content] = next_index
                renumbered.append(f"[{next_index}] {content}")
                next_index += 1
            mapping[f"{paragraph_no}-{match.group(1)}"] = seen[content]

    return "\n\n".join(renumbered), mapping


def _deduplicate_and_renumber_ref(raw_text: str) -> tuple[str, dict[str, int]]:
    """单段文本入口，保留给旧调用方；新代码请用 :func:`_deduplicate_and_renumber_refs`。

    空行分隔的多个引用块按顺序当作多个段落，段落号不再随空行数漂移。
    """
    blocks = [b for b in re.split(r"\n\s*\n", raw_text.strip())] if raw_text.strip() else []
    return _deduplicate_and_renumber_refs(blocks)


def _replace_citations_and_classified_index(
    paragraphs: list[str],
    classified_contents: list[list[dict]],
    ref_map: dict[str, int],
) -> tuple[list[str], list[list[dict]]]:
    if not ref_map or not classified_contents:
        return paragraphs, classified_contents

    updated_paragraphs: list[str] = []
    updated_classified_contents: list[list[dict]] = []

    for i, para in enumerate(paragraphs):
        if i >= len(classified_contents):
            updated_paragraphs.append(para)
            continue
        sub_classified_contents = classified_contents[i]
        if not sub_classified_contents:
            updated_paragraphs.append(para)
            updated_classified_contents.append([])
            continue

        # Build index mapping: original index -> new number
        index_map = {
            str(item["index"]): ref_map.get(f"{i + 1}-{item['index']}")
            for item in sub_classified_contents
        }

        # 单次替换：逐条 re.sub 会把上一轮刚换好的编号当成原文编号再换一次
        # （1->2、2->3 时 [citation:1] 会被连换两次）。
        updated_para = _CITATION_RE.sub(
            lambda m: (
                f"[citation:{index_map[m.group(1)]}]"
                if index_map.get(m.group(1)) is not None
                else m.group(0)
            ),
            para,
        )
        updated_paragraphs.append(updated_para)

        # Update index field in reference entries
        updated_sub_classified_content: list[dict] = []
        for item in sub_classified_contents:
            updated_item = item.copy()
            final_index = index_map.get(str(item["index"]))
            if final_index is not None:
                updated_item["index"] = final_index
            updated_sub_classified_content.append(updated_item)

        updated_classified_contents.append(updated_sub_classified_content)

    return updated_paragraphs, updated_classified_contents


class ReferenceMixin:
    """Mixin for reference management operations."""

    @staticmethod
    def add_references(sub_section_content: str, references: list, language: str):
        """Add references for subsection content"""
        logger.info(f"Adding references to sub_section_content")
        if not references:
            logger.info(f"No references found. can not add references.")
            return sub_section_content
        if sub_section_content:
            if language == CHINESE:
                append = "\n## 参考文章\n"
            else:
                append = "\n## References\n"
            temp_ref = "\n".join(f"[{i + 1}] {s}" for i, s in enumerate(references))
            sub_section_content = sub_section_content + append + temp_ref
        return sub_section_content

    @staticmethod
    def refresh_reference(sub_reports_content, sub_references, all_classified_contents):
        """Refresh references"""
        refreshed_references = ""
        raw_references = "\n".join(sub_references) if sub_references else ""
        if raw_references:
            refreshed_references, ref_map = _deduplicate_and_renumber_refs(
                sub_references
            )
            if not LogManager.is_sensitive():
                logger.info("refreshed_references: [%s]", refreshed_references)
            sub_reports_content, all_classified_contents = (
                _replace_citations_and_classified_index(
                    sub_reports_content, all_classified_contents, ref_map
                )
            )

        return dict(
            sub_reports_content="\n\n".join(sub_reports_content),
            sub_references=refreshed_references,
            refreshed_all_classified_contents=all_classified_contents,
        )
