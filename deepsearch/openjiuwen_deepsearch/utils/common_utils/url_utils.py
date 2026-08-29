# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import ipaddress
import logging
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse, urlunparse

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH

logger = logging.getLogger(__name__)

# 安全相关的URL scheme白名单
SAFE_URL_SCHEMES = frozenset(['http', 'https'])


def validate_and_sanitize_url(url: str) -> str:
    """
    验证URL并确保只允许安全的scheme (http/https)

    Args:
        url: 待验证的URL

    Returns:
        str: 安全的URL，若scheme不合法则返回空字符串
    """
    if not url:
        return ""

    # 解析URL获取scheme
    url_stripped = url.strip()

    # 检查是否以合法scheme开头
    # 格式: scheme://...
    scheme_match = re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*)://', url_stripped)

    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme not in SAFE_URL_SCHEMES:
            logger.warning(
                f"URL scheme '{scheme}' is not allowed, URL blocked: "
                f"{url_stripped[:100]}"
            )
            return ""
        return url_stripped
    else:
        # 没有scheme的相对路径或不完整URL，视为不安全
        logger.warning(
            f"URL without valid scheme blocked: {url_stripped[:100]}"
        )
        return ""


def validate_url_scheme(url: str) -> tuple:
    """
    验证URL scheme是否在白名单中 (http/https)

    Args:
        url: 待验证的URL

    Returns:
        tuple: (safe_url, is_valid)
            - safe_url: 验证后的URL，若scheme不合法则返回空字符串
            - is_valid: True表示scheme合法，False表示不合法
    """
    if not url:
        return "", False

    url_stripped = url.strip()

    # 检查是否以合法scheme开头
    scheme_match = re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*)://', url_stripped)

    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme not in SAFE_URL_SCHEMES:
            logger.warning(
                f"URL scheme '{scheme}' is not allowed, "
                f"URL blocked: {url_stripped[:100]}"
            )
            return "", False
        return url_stripped, True
    else:
        # 没有scheme的相对路径或不完整URL，视为不安全
        logger.warning(
            f"URL without valid scheme blocked: {url_stripped[:100]}"
        )
        return "", False


def normalize_domain(domain: str) -> str:
    """
    规范化域名，处理常见的幻觉域名错误

    Args:
        domain: 原始域名

    Returns:
        规范化后的域名
    """
    # 处理域名中的连字符错误
    patterns = [
        # 处理域名后缀被错误添加为子域名的情况
        (r'\.com-([a-z]+)$', r'.com'),
        (r'\.net-([a-z]+)$', r'.net'),
        (r'\.org-([a-z]+)$', r'.org'),
        (r'\.edu-([a-z]+)$', r'.edu'),
        (r'\.gov-([a-z]+)$', r'.gov'),
        # 处理连字符连接的域名部分，但保留实际的路径
        (r'\.([a-z]+)-([a-z]+)$', r'.\1'),
        (r'-([a-z]+)$', r''),
    ]

    normalized = domain
    for pattern, replacement in patterns:
        normalized = re.sub(pattern, replacement, normalized)

    return normalized


def normalize_domains(domains: Any) -> list[str]:
    """归一化域名列表."""
    if not domains:
        return []
    if isinstance(domains, str):
        domains = [domains]
    if not isinstance(domains, (list, tuple, set)):
        return []

    normalized_domains = []
    seen = set()
    for domain in domains:
        domain_str = str(domain).strip().lower()
        parsed = urlparse(domain_str if "://" in domain_str else f"//{domain_str}")
        domain_str = parsed.netloc or parsed.path
        domain_str = domain_str.split("@")[-1].split(":")[0].strip(".")
        if domain_str.startswith("www."):
            domain_str = domain_str[4:]
        if not domain_str or domain_str in seen:
            continue
        seen.add(domain_str)
        normalized_domains.append(domain_str)
    return normalized_domains


def extract_domain_from_url(url: Any) -> str:
    """从 URL 中提取域名."""
    url_str = str(url or "").strip().lower()
    if not url_str:
        return ""
    parsed = urlparse(url_str if "://" in url_str else f"//{url_str}")
    domain = (parsed.netloc or "").split("@")[-1].split(":")[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def normalize_url_for_match(url: Any) -> str:
    """归一化 URL 用于等价匹配.

    返回 ``host + path``（小写、去 ``www.``、路径合并重复斜杠并去末尾斜杠），
    忽略 scheme、query 和 fragment，使同一页面的 http/https、带参链接等变体能匹配上。
    """
    url_str = str(url or "").strip()
    if not url_str:
        return ""
    parsed = urlparse(url_str if "://" in url_str else f"//{url_str}")
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or any(c.isspace() for c in host):
        return ""
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/").lower()
    return f"{host}{path}"


def is_url_blocked(url: Any, blocked_urls: Any) -> bool:
    """判断 URL 是否命中用户要求排除的链接列表.

    归一化（见 ``normalize_url_for_match``）后与禁引列表中任一 URL 完全相同即命中；
    只做 host+path 精确匹配，同域名下路径不同的其他文章不受影响。
    """
    if not blocked_urls:
        return False
    if isinstance(blocked_urls, str):
        blocked_urls = [blocked_urls]
    target = normalize_url_for_match(url)
    if not target:
        return False
    return any(target == normalize_url_for_match(blocked) for blocked in blocked_urls)


def normalize_path(path: str) -> str:
    """
    规范化路径，处理路径中的错误

    Args:
        path: 原始路径

    Returns:
        规范化后的路径
    """
    # 处理路径中的双斜杠
    if len(path) > MAX_URL_LENGTH:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_URL_EXCEED_LENGTH.code,
            message=StatusCode.PARAM_CHECK_ERROR_URL_EXCEED_LENGTH.errmsg)

    path = re.sub(r'/+', '/', path)

    # 确保路径以/开头
    if not path.startswith('/'):
        path = '/' + path

    return path


def fix_domain_path_merge(url: str) -> str:
    """
    修复域名和路径被错误合并的问题

    Args:
        url: 原始URL

    Returns:
        修复后的URL
    """
    if len(url) > MAX_URL_LENGTH:
        raise CustomValueException(
            error_code=StatusCode.PARAM_CHECK_ERROR_URL_EXCEED_LENGTH.code,
            message=StatusCode.PARAM_CHECK_ERROR_URL_EXCEED_LENGTH.errmsg)

    pattern = r'https?://([^/]+)-([a-z]+)/(.+)'
    match = re.match(pattern, url)
    if match:
        domain, path_prefix, rest_path = match.groups()
        return f'https://{domain}/{path_prefix}/{rest_path}'

    return url


def normalize_url(url: str) -> str:
    """
    规范化URL，处理幻觉产生的URL错误

    Args:
        url: 原始URL

    Returns:
        规范化后的URL
    """
    try:
        # 首先修复域名和路径的合并问题
        fixed_url = fix_domain_path_merge(url)

        parsed = urlparse(fixed_url)

        # 规范化域名
        normalized_domain = normalize_domain(parsed.netloc)

        # 规范化路径
        normalized_path = normalize_path(parsed.path)

        # 重新构建URL
        normalized_url = urlunparse((
            parsed.scheme or 'https',
            normalized_domain,
            normalized_path,
            parsed.params,
            parsed.query,
            parsed.fragment
        ))

        return normalized_url

    except Exception:
        # 如果解析失败，返回原始URL
        return url


def are_similar_urls(url1: str, url2: str, threshold: float = 0.9) -> bool:
    """
    判断两个URL是否相似（可能是同一个网页的不同表示）

    Args:
        url1: 第一个URL
        url2: 第二个URL
        threshold: 相似度阈值

    Returns:
        是否相似的布尔值
    """
    try:
        norm_url1 = normalize_url(url1)
        norm_url2 = normalize_url(url2)

        # 如果规范化后的URL完全相同
        if norm_url1 == norm_url2:
            return True

        # 计算路径相似度
        parsed1 = urlparse(norm_url1)
        parsed2 = urlparse(norm_url2)

        # 检查域名和路径的相似度
        domain_similar = parsed1.netloc == parsed2.netloc
        path_similar = parsed1.path == parsed2.path

        if domain_similar and path_similar:
            return True

        # 计算更细致的相似度
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, norm_url1, norm_url2).ratio()

        return similarity >= threshold

    except Exception:
        return False


def _http_service_allow_unsafe_url(env_var: str) -> bool:
    value = os.environ.get(env_var, "").strip().lower()
    return value in ("1", "true", "yes")


def _is_runtime_api_unsafe_url_relaxed() -> bool:
    return _http_service_allow_unsafe_url("RUNTIME_API_ALLOW_UNSAFE_URL")


def _unsafe_http_service_url_exception_detail(
    url: str, reason: str, service_label: str
) -> str:
    return f"{service_label} is not allowed ({reason}): {url!r}"


def _validate_http_url_for_ssrf(
    url: str, *, relaxed: bool, service_label: str
) -> None:
    if relaxed:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                e=_unsafe_http_service_url_exception_detail(
                    url, "scheme must be http or https", service_label
                )
            ),
        )
    host = parsed.hostname
    if not host:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                e=_unsafe_http_service_url_exception_detail(
                    url, "missing host", service_label
                )
            ),
        )
    host_lower = host.lower()
    if host_lower == "localhost" or host_lower.endswith(".localhost"):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                e=_unsafe_http_service_url_exception_detail(
                    url, "localhost host is blocked", service_label
                )
            ),
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as host_parse_error:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addr_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (ValueError, socket.gaierror) as error:
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e=_unsafe_http_service_url_exception_detail(
                        url, f"host resolution failed: {error}", service_label
                    )
                ),
            ) from error

        addresses = {entry[4][0] for entry in addr_info if entry[4]}
        if not addresses:
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e=_unsafe_http_service_url_exception_detail(
                        url, "host resolution returned no addresses", service_label
                    )
                ),
            ) from host_parse_error

        for address in addresses:
            try:
                resolved_ip = ipaddress.ip_address(address)
            except ValueError as error:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                        e=_unsafe_http_service_url_exception_detail(
                            url, f"invalid resolved address: {address}", service_label
                        )
                    ),
                ) from error

            is_non_public_ip = any((
                resolved_ip.is_private,
                resolved_ip.is_loopback,
                resolved_ip.is_link_local,
                resolved_ip.is_multicast,
                resolved_ip.is_reserved,
                resolved_ip.is_unspecified,
            ))
            if is_non_public_ip:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                        e=_unsafe_http_service_url_exception_detail(
                            url, "private or non-public IP", service_label
                        )
                    ),
                ) from host_parse_error
        return

    is_non_public_ip = any((
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ))
    if is_non_public_ip:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                e=_unsafe_http_service_url_exception_detail(
                    url, "private or non-public IP", service_label
                )
            ),
        )


def validate_runtime_request_url(url: str) -> None:
    """
    Validate runtime API request URL to reduce SSRF risk.
    Local debugging can bypass this check with RUNTIME_API_ALLOW_UNSAFE_URL.
    """
    _validate_http_url_for_ssrf(
        url,
        relaxed=_is_runtime_api_unsafe_url_relaxed(),
        service_label="runtime api url",
    )


def validate_scholarly_full_text_url(url: str) -> None:
    """Require a public HTTP(S) destination for scholarly full-text downloads."""
    _validate_http_url_for_ssrf(
        url,
        relaxed=False,
        service_label="scholarly full-text url",
    )


def validate_embedding_service_url(url: str) -> None:
    """
    Validate embedding HTTP service base URL to reduce SSRF risk.
    Local debugging can bypass with EMBEDDING_SERVICE_ALLOW_UNSAFE_URL=1
    (same accepted values as RUNTIME_API_ALLOW_UNSAFE_URL).
    """
    _validate_http_url_for_ssrf(
        url,
        relaxed=_http_service_allow_unsafe_url("EMBEDDING_SERVICE_ALLOW_UNSAFE_URL"),
        service_label="embedding service url",
    )
