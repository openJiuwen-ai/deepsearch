# -*- coding: UTF-8 -*-
"""Embedding API 与 EMBEDDING_SSL_* 环境变量相关的工具函数。"""
import os
from typing import Any, Optional
from urllib.parse import urlparse

from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_embedding_service_url


def get_embedding_requests_verify(base_url: Optional[str] = None) -> Any:
    """
    返回 requests 的 verify 参数：

    - False  → 不校验 SSL
    - True   → 使用系统 CA
    - str    → 使用指定证书路径

    未设置 EMBEDDING_SSL_VERIFY 或值为空白时，视为关闭校验（与 .env.example 默认一致）。

    ssl_verify：是否启用 SSL 证书校验（与变量名语义一致）；为 True 时再根据证书路径选择 verify。
    """

    api_url = (base_url or "").strip()
    if api_url:
        validate_embedding_service_url(api_url)

    scheme = urlparse(api_url).scheme.lower() if api_url else ""

    # HTTP 不涉及 SSL（返回 True/False 都可以，这里用 True 保持语义一致）
    if scheme != "https":
        return True

    raw_verify = os.getenv("EMBEDDING_SSL_VERIFY", "").strip()
    if not raw_verify:
        ssl_verify = False
    else:
        ssl_verify = raw_verify.lower() not in ("false",)

    ssl_cert = os.getenv("EMBEDDING_SSL_CERT")

    if not ssl_verify:
        return False

    if ssl_cert:
        return ssl_cert

    return True
