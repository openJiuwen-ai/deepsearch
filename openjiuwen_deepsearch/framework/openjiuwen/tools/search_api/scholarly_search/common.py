# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Union

from openjiuwen.core.common.security.ssl_utils import SslUtils

DEFAULT_PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"
ATOM_NAMESPACE = "http" + "://www.w3.org/2005/Atom"


class ScholarlySearchResponseError(RuntimeError):
    """Raised when a scholarly search API returns a malformed or unexpected response."""


def truncate(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def ssl_verify() -> Union[str, bool]:
    ssl_verify_value, ssl_cert = SslUtils.get_ssl_config("TOOL_SSL_VERIFY", "TOOL_SSL_CERT", ["false"])
    return ssl_cert if ssl_verify_value else False
