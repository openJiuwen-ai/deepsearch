# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Background knowledge extraction and formatting for report generation."""

import json
import logging

logger = logging.getLogger(__name__)


class BackgroundKnowledgeMixin:
    """Mixin for background knowledge processing."""

    @staticmethod
    def _get_background_knowledge_contents(background_knowledge: list) -> list[dict[str, str]]:
        """Extract usable text snippets from dependency-writing background knowledge."""
        if not isinstance(background_knowledge, list):
            return []

        contents = []
        for item in background_knowledge:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content_summary", "") or "").strip()
            if not content:
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            allowed_callback = (
                f"Refer to Section {section_id} in natural prose only; do not cite this context."
                if section_id
                else "Refer to prior sections in natural prose only; do not cite this context."
            )
            contents.append(
                {
                    "section_id": section_id,
                    "summary": content,
                    "allowed_callback": allowed_callback,
                }
            )

        return contents

    @staticmethod
    def _format_background_knowledge_for_prompt(background_knowledge_contents: list[dict[str, str]]) -> str:
        """Format prior-section background knowledge for model input without citation-like labels."""
        if not background_knowledge_contents:
            return (
                "Background Knowledge / prior-section continuity context "
                "(not citation sources): []"
            )
        payload = json.dumps(background_knowledge_contents, ensure_ascii=False, indent=2)
        return (
            "Background Knowledge / prior-section continuity context (not citation sources):\n"
            f"{payload}\n"
            "Rules for this context:\n"
            "- Use it only to maintain continuity with earlier sections.\n"
            "- You may refer to it in natural prose, such as \"as discussed in Section 2\" "
            "or \"结合第2章分析\".\n"
            "- Never cite it with `[citation:X]`.\n"
            "- Never output bracketed labels about this context."
        )
