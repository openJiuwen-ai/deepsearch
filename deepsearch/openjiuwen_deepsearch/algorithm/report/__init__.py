# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen_deepsearch.algorithm.report import compact_doc_info
from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_classify_scores,
    build_compact_classify_doc_infos_text,
    build_key_passage_text,
    format_scores_inline,
    format_key_passage_block,
    get_numeric_score,
    normalize_key_passages,
)

__all__ = [
    "build_classify_scores",
    "build_compact_classify_doc_infos_text",
    "build_key_passage_text",
    "compact_doc_info",
    "format_scores_inline",
    "format_key_passage_block",
    "get_numeric_score",
    "normalize_key_passages",
]
