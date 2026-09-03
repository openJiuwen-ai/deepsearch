# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Constants, regex patterns, and module-level utility functions for report generation."""

import json
import logging
import re

from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

EFFECT_SUB_REPORT_TAG = "### sub_report_tag ###"
MAX_CONCURRENT_BATCHES = 5
EXTRACT_BATCH_SIZE = 5  # documents per batch for extractive summarization + scoring
MAX_EXTRACT_DOC_CHARS = 15000  # max content chars per document sent to LLM

# 可视化内容生成的最大并发LLM调用数。
# 限制同时发起的数据提取/校验请求，避免触发模型API的TPM（Tokens Per Minute）限制。
MAX_CONCURRENT_VISUALIZATION_TASKS = 5

# ── Regex patterns ─────────────────────────────────────────────────────────

LEADING_TITLE_NUMBER_PATTERN = re.compile(
    r"^(?:"
    r"[\（][一二三四五六七八九十\d]{1,2}[\）]\s*|"
    r"[\(][一二三四五六七八九十\d]{1,2}[\)]\s*|"
    r"第?[一二三四五六七八九十\d]+章\s*|"
    r"[一二三四五六七八九十]+、\s*|"
    r"\d{1,2}(?:\.\d{1,2})+\s*(?![\da-zA-Z.])|"
    r"\d{1,2}[\.、]\s*(?![\da-zA-Z.])|"
    r"(?:[1-9]|1\d)\s+|"
    r")"
)
INTERNAL_CALLBACK_LABEL_PATTERN = re.compile(
    r"\s*\["
    r"(?=[^\]]*(?:background|knowledge|parent|section|prior|summary|背景|知识))"
    r"[^\]]+"
    r"\]\s*",
    re.IGNORECASE,
)
MERMAID_SYNTAX_LINE_PATTERN = re.compile(
    r"(?im)^\s*(?:"
    r"(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b|"
    r"(?:sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|erDiagram|"
    r"mindmap|quadrantChart|xychart-beta|sankey-beta|block-beta|"
    r"gitGraph|C4Context)\s*$|"
    r"(?:journey|gantt|pie|timeline)"
    r"(?:\s+(?:title|showData)\b.*)?\s*$"
    r")"
)
FENCED_BLOCK_PATTERN = re.compile(
    r"(?ms)^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^\r\n]*)\r?\n"
    r"(?P<body>.*?)[ \t]*(?P=fence)[ \t]*$"
)

# Maximum characters allowed in a rationale description.
MAX_RATIONALE_DESC_LEN = 200

#: content_date 选材的时序分权重上限（排序键 = 覆盖分 + 权重×时效分，
#: 实际权重 = 该值 × 候选池有日期段落占比，信号越少自动趋零）；
#: 数值即时间分能反超的覆盖度差距上限。
CONTENT_DATE_TIMELINESS_WEIGHT = 0.2

#: 选材保留门的覆盖度下限：须与 report_rationale_fulltext.filter_passages_by_coverage
#: 的 threshold（0.15）保持一致。对齐后本函数选出的段落必然通过下游 Layer 1 过滤，
#: 时间加权的提升结果不会被下游纯覆盖度过滤撤销。
SELECTION_COVERAGE_FLOOR = 0.15

#: 下游 Layer 2（dedup_passages_by_rationale）每个 rationale 的截断上限，须与
#: report_rationale_fulltext.enrich_fulltext_for_section 的 top_k_per_rationale（15）
#: 保持一致。并集补回按"每 rationale 交付不超过该值"封顶，使 Layer 2 的
#: 纯覆盖度重排截断对单 rationale 新增交付不触发（以 top_k ≤ 该值为前提；
#: top_k 更大时主循环自身即可能超 15 条，截断重新生效；跨 rationale 共享段落
#: 导致的超 15 属已接受的二阶边缘场景）。
FULLTEXT_TOP_K_PER_RATIONALE = 15


# ── Module-level utility functions ──────────────────────────────────────────

def _format_report_error(detail: str | BaseException) -> str:
    return format_exception_info(StatusCode.REPORT_GENERATE_ERROR, detail)


def _format_sub_report_error(detail: str | BaseException) -> str:
    return format_exception_info(StatusCode.SUB_REPORT_GENERATE_ERROR, detail)


def build_citation_infos(classified_content: list) -> str:
    """Build the ``infos`` citation string consumed by the sub-report writer.

    Each classified item is rendered as a single citation block:
    ``[citation:X begin]publish_time: ...|||content_time: start~end|||source: ...
    |||scores: ...|||content: ...[citation:X end]``.

    ``content_time`` (the fact-level time window) is only rendered when the
    item carries a ``content_time`` dict with a non-empty ``start`` — i.e.
    under a ``content_date`` temporal scope. It is ``None`` (and therefore
    omitted) for ``source_date`` scope and for full-text items, so the block
    stays publication-time-only in those cases.
    """
    infos = ""
    for item in classified_content or []:
        content = item.get("passage_text", "") or item.get("original_content", "")
        scores_str = ""
        if item.get("scores"):
            scores_str = f"|||scores: {json.dumps(item['scores'], ensure_ascii=False)}"
        content_time = item.get("content_time")
        content_time_str = ""
        if isinstance(content_time, dict) and content_time.get("start"):
            content_time_str = (
                f"|||content_time: {content_time.get('start', '')}"
                f"~{content_time.get('end', '')}"
            )
        infos += (
            f"\n[citation:{item.get('index', 1)} begin]publish_time: "
            f"{item.get('doc_time') or ''}{content_time_str}|||"
            f"source: {item.get('title', '')}{scores_str}|||"
            f"content: {content}[citation:{item.get('index', 1)} end]"
        )
    return infos
