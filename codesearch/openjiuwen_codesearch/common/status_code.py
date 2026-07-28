# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""错误码表。命名与结构对齐 deepsearch 的 common/status_code.py 惯例。"""

from enum import Enum


class StatusCode(Enum):
    # (code, errmsg)
    PARAM_ERROR = (110001, "Invalid parameter: {e}")
    CONFIG_ERROR = (110002, "Invalid configuration: {e}")
    INDEX_NOT_READY = (120001, "Index not ready for revision '{revision}': {e}")
    RETRIEVAL_ERROR = (120002, "Retrieval failed: {e}")
    INDEXING_ERROR = (120003, "Indexing failed: {e}")
    EMBEDDING_ERROR = (120004, "Embedding request failed after {retries} retries: {e}")
    LLM_ERROR = (130001, "LLM invocation failed: {e}")
    TOOL_PARSE_ERROR = (130002, "Failed to parse tool call arguments: {e}")
    AGENT_ERROR = (130003, "Agent run failed: {e}")

    @property
    def code(self) -> int:
        return self.value[0]

    @property
    def errmsg(self) -> str:
        return self.value[1]
