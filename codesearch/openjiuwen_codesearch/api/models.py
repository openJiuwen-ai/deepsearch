# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""公共 API 输入输出模型（re-export 领域模型 + 索引报告）。"""

from pydantic import BaseModel

from openjiuwen_codesearch.domain.result import CodeSearchResult, FinalHit, Termination

__all__ = ["CodeSearchResult", "FinalHit", "Termination", "IndexReport"]


class IndexReport(BaseModel):
    files_total: int = 0
    files_new: int = 0
    files_reused: int = 0
    chunks_inserted: int = 0
