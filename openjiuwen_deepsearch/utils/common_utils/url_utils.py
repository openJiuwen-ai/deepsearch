# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from urllib.parse import urlparse, urlunparse

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH


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
