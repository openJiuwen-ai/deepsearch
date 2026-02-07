import pytest

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.utils.common_utils.url_utils import (
    fix_domain_path_merge,
    normalize_path,
    normalize_domain,
    normalize_url,
    are_similar_urls
)

# 构造一个长度超过 8192 的合法 URL
scheme = "https"
host = "example.com"
path = "/search"
query_key = "q"
query_value = "A" * 8200  # 确保总长度远超 8192
url = f"{scheme}://{host}{path}?{query_key}={query_value}"


# 测试 normalize_domain 函数
@pytest.mark.parametrize("domain, expected", [
    # 测试正常域名
    ("example.com", "example.com"),
    ("sub.example.com", "sub.example.com"),
    # 测试域名后缀被错误添加为子域名的情况
    ("example.com-www", "example.com"),
    ("example.net-api", "example.net"),
    ("example.org-docs", "example.org"),
    ("example.edu-courses", "example.edu"),
    ("example.gov-info", "example.gov"),
    # 测试连字符连接的域名部分（注意：这些情况不会被规范化）
    ("example-api.com", "example-api.com"),
    ("example-docs.com", "example-docs.com"),
    ("example-www", "example"),
    # 测试复杂情况（注意：这种情况不会被规范化）
    ("api.example.com-v1", "api.example.com-v1"),
])
def test_normalize_domain(domain, expected):
    result = normalize_domain(domain)
    assert result == expected


# 测试 normalize_path 函数
@pytest.mark.parametrize("path, expected", [
    # 测试正常路径
    ("/path/to/resource", "/path/to/resource"),
    ("path/to/resource", "/path/to/resource"),
    # 测试路径中的双斜杠
    ("/path//to//resource", "/path/to/resource"),
    ("//path//to//resource", "/path/to/resource"),
    # 测试单字符路径
    ("a", "/a"),
    ("/", "/"),
    # 测试空路径
    ("", "/"),
    # 测试复杂路径
    ("/api/v1/users//123/", "/api/v1/users/123/"),
])
def test_normalize_path(path, expected):
    result = normalize_path(path)
    assert result == expected


@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (url, 200021, "URL length must be less than 8192"),
])
def test_normalize_path_length_exceeded(invalid_value, error_code, error_msg_fragment):
    with pytest.raises(CustomValueException) as exc_info:
        normalize_path(invalid_value)
    err_msg = str(exc_info.value)
    assert exc_info.value.error_code == error_code
    assert error_msg_fragment in err_msg


# 测试 fix_domain_path_merge 函数
@pytest.mark.parametrize("url, expected", [
    # 测试正常URL（不匹配模式，返回原始URL）
    ("https://example.com/path/to/resource", "https://example.com/path/to/resource"),
    ("http://example.com/path/to/resource", "http://example.com/path/to/resource"),
    # 测试域名和路径被错误合并的情况（只匹配特定模式）
    ("https://example.com-api/v1/users", "https://example.com/api/v1/users"),
    ("https://example.org-docs/v2/data", "https://example.org/docs/v2/data"),
    ("https://example.net-api/v1/endpoints", "https://example.net/api/v1/endpoints"),
    # 测试不匹配模式的复杂情况（返回原始URL）
    ("https://api.example.com-v1/users/123", "https://api.example.com-v1/users/123"),
    # 注意：这个URL匹配模式，会被修正
    ("https://sub.example.com-docs/v1/data/items", "https://sub.example.com/docs/v1/data/items"),
])
def test_fix_domain_path_merge(url, expected):
    result = fix_domain_path_merge(url)
    assert result == expected


# 测试 normalize_url 函数
@pytest.mark.parametrize("url, expected", [
    # 测试正常URL
    ("https://example.com/path/to/resource", "https://example.com/path/to/resource"),
    ("http://example.org/api/v1/data", "http://example.org/api/v1/data"),
    # 测试需要域名规范化的URL（先通过fix_domain_path_merge修正）
    ("https://example.com-api/v1/users", "https://example.com/api/v1/users"),
    ("https://example.org-docs/v2/data", "https://example.org/docs/v2/data"),
    # 测试需要路径规范化的URL
    ("https://example.com//path//to//resource", "https://example.com/path/to/resource"),
    ("https://example.com/path//to//resource", "https://example.com/path/to/resource"),
    # 测试需要同时规范化的URL
    ("https://example.com-api//path//to//resource", "https://example.com/api/path/to/resource"),
    # 测试缺少协议的URL（会被添加默认协议）
    ("example.com/path/to/resource", "https:///example.com/path/to/resource"),
    # 测试带查询参数的URL
    ("https://example.com/api?query=test", "https://example.com/api?query=test"),
    ("https://example.com-api//api?query=test", "https://example.com/api/api?query=test"),
    # 测试带片段的URL
    ("https://example.com/page#section", "https://example.com/page#section"),
    ("https://example.com-api//page#section", "https://example.com/api/page#section"),
])
def test_normalize_url(url, expected):
    result = normalize_url(url)
    assert result == expected


# 测试 normalize_url 函数的异常处理
@pytest.mark.parametrize("invalid_url, expected", [
    # 测试无效URL格式（函数会尝试规范化而不是返回原始URL）
    ("not-a-valid-url", "https:///not-a-valid-url"),
    ("://missing-protocol.com", "https:///:/missing-protocol.com"),
    ("https://", "https:///"),
    # 测试空字符串
    ("", "https:///"),
])
def test_normalize_url_invalid_urls(invalid_url, expected):
    # 对于无效URL，函数会尝试规范化而不是返回原始URL
    result = normalize_url(invalid_url)
    assert result == expected


# 测试 are_similar_urls 函数
@pytest.mark.parametrize("url1, url2, expected", [
    # 测试完全相同的URL
    ("https://example.com/path/to/resource", "https://example.com/path/to/resource", True),
    # 测试规范化后相同的URL
    ("https://example.com-api//path//to//resource", "https://example.com/path/to/resource", True),
    ("http://example.org/api/v1/data", "example.org/api/v1/data", True),
    # 测试域名和路径相同但查询参数不同
    ("https://example.com/api?query=test", "https://example.com/api?query=other", True),
    # 测试域名和路径相同但片段不同
    ("https://example.com/page#section1", "https://example.com/page#section2", True),
    # 测试相似的URL
    ("https://example.com/path/to/resource", "https://example.com/path/to/resource/", True),
    # 测试不相似的URL
    ("https://example.com/path/to/resource", "https://different.com/path/to/resource", False),
    ("https://example.com/path/to/resource", "https://example.com/different/path", False),
])
def test_are_similar_urls(url1, url2, expected):
    result = are_similar_urls(url1, url2)
    assert result == expected


# 测试 are_similar_urls 函数的阈值参数
@pytest.mark.parametrize("url1, url2, threshold, expected", [
    # 测试不同阈值的情况
    ("https://example.com/path/to/resource", "https://example.com/path/to/other", 0.5, True),
    ("https://example.com/path/to/resource", "https://example.com/path/to/other", 0.9, False),
    # 测试极端阈值
    ("https://example.com/path/to/resource", "https://example.com/path/to/resource", 1.0, True),
])
def test_are_similar_urls_threshold(url1, url2, threshold, expected):
    result = are_similar_urls(url1, url2, threshold)
    assert result == expected


# 测试 are_similar_urls 函数的异常处理
@pytest.mark.parametrize("url1, url2", [
    # 测试无效URL
    ("not-a-valid-url", "https://example.com/path"),
    ("https://example.com/path", "not-a-valid-url"),
    ("not-a-valid-url", "another-invalid-url"),
])
def test_are_similar_urls_invalid_urls(url1, url2):
    # 对于无效URL，函数应该返回False而不是抛出异常
    result = are_similar_urls(url1, url2)
    assert result is False

