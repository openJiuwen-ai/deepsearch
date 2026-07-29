# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Milvus 表达式构造 —— 由 openjiuwen-search-base 提供（2026-07-29 提取）。
保留原 import 路径作为薄壳；所有 expr 仍必须经这些构造函数（统一转义防注入）。
"""

from openjiuwen_search_base.milvus import (  # noqa: F401  re-export
    escape_expr_string,
    hashes_filter,
    ids_filter,
    overlap_filter,
    revision_filter,
)
