# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""检索层。`base` 定义协议与 fake；`milvus` 子包为真实现（依赖 pymilvus，按需 import）。
注意：不要在此 import milvus 子包，保持核心可在无 pymilvus 环境下使用。
"""

from openjiuwen_codesearch.retrieval.base import CodeRetriever, InMemoryRetriever

__all__ = ["CodeRetriever", "InMemoryRetriever"]
