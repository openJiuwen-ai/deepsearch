# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reference management: deduplication, renumbering, and citation replacement."""

import logging
import re

from openjiuwen_deepsearch.common.common_constants import CHINESE
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def _deduplicate_and_renumber_ref(raw_text: str) -> tuple[str, dict[str, int]]:
    lines = raw_text.splitlines()
    seen = {}
    result = []
    mapping = {}
    index = 1
    paragraph_id = 0

    for line in lines:
        line = line.strip()
        if not line:
            paragraph_id += 1  # empty line is one section too
            continue

        # test if new section（start with [1]）
        if re.match(r"^\[1\]", line):
            ref_index = 1
            paragraph_id += 1
        else:
            # get original ref no
            match = re.match(r"^\[(\d+)\]", line)
            if match:
                ref_index = int(match.group(1))
            else:
                continue

        # remove original no
        content = re.sub(r"^\[\d+\]\s*", "", line).strip()

        key = f"{paragraph_id}-{ref_index}"
        # add ref content to non-duplicate array
        if content not in seen:
            seen[content] = index
            result.append(f"[{index}] {content}")
            index += 1

        mapping[key] = seen[content]

    return "\n\n".join(result), mapping


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

        # Replace citations in the loop without a closure
        updated_para = para
        for original_index, final_index in index_map.items():
            if final_index is not None:
                updated_para = re.sub(
                    rf"\[citation:{original_index}\]",
                    f"[citation:{final_index}]",
                    updated_para,
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
            refreshed_references, ref_map = _deduplicate_and_renumber_ref(
                raw_references
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
