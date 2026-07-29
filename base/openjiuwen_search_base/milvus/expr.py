# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Milvus 表达式安全构造。所有 expr 必须经本模块——统一转义，杜绝 f-string 直拼注入。"""


def escape_expr_string(value: str) -> str:
    """转义进入 Milvus expr 双引号字符串字面量的值。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def revision_filter(revision: str) -> str:
    return f'ARRAY_CONTAINS(commits, "{escape_expr_string(revision)}")'


def overlap_filter(revision: str, file_path: str, start_line: int, end_line: int) -> str:
    return (
        f"{revision_filter(revision)} and "
        f'file_path == "{escape_expr_string(file_path)}" and '
        f"start_line <= {int(end_line)} and end_line >= {int(start_line)}"
    )


def hashes_filter(file_hashes: list[str]) -> str:
    quoted = ",".join(f'"{escape_expr_string(h)}"' for h in file_hashes)
    return f"file_hash in [{quoted}]"


def ids_filter(ids: list[int]) -> str:
    joined = ",".join(str(int(i)) for i in ids)
    return f"id in [{joined}]"
