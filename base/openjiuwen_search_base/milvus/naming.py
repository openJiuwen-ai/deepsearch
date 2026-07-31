# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Collection 命名约定：产品前缀 + schema 版本。

这是**多产品共用一个 Milvus 实例**的隔离基础：各产品只操作自己前缀下的
collection（如 codesearch 的 `cs_`），互不触碰；schema 演进以版本后缀承载，
禁止静默复用旧结构。
"""


def versioned_collection_name(base_name: str, schema_version: str, prefix: str) -> str:
    return f"{prefix}{base_name}__{schema_version}"
