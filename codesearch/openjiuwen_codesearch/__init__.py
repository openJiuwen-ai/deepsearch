# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""openJiuwen-CodeSearch: agentic code retrieval.

Public surface: `CodeSearchRetriever` (+ legacy alias `JiuwenRetriever`).
"""

from pathlib import Path

from openjiuwen_codesearch.api.retriever import CodeSearchRetriever, JiuwenRetriever
from openjiuwen_codesearch.config.config import CodeSearchConfig

# codesearch/.env (this file lives in openjiuwen_codesearch/)
_CODESEARCH_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ENV_FILE = _CODESEARCH_ROOT / ".env"
_DOTENV_LOADED = False

__all__ = ["CodeSearchRetriever", "JiuwenRetriever", "CodeSearchConfig"]
