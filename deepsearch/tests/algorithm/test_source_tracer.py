"""SourceTracer.transform_search_record 及 _build_citation_mapping 的单元测试。"""

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_tracer import SourceTracer
from openjiuwen_deepsearch.algorithm.source_trace.source_tracer_preprocessors import _build_citation_mapping


class TestTransformSearchRecord:
    """transform_search_record 的测试用例，覆盖新旧字段格式、去重与异常输入处理。"""

    @staticmethod
    def test_new_format_fields():
        """新字段名(doc_url/doc_title/passage_text)应正确映射为 url/title/content。"""
        classified_content = [
            {
                "doc_url": "https://example.com/a",
                "doc_title": "新格式标题",
                "passage_text": "新格式正文",
            }
        ]
        expected = {
            "search_record": [
                {"url": "https://example.com/a", "title": "新格式标题", "content": "新格式正文"}
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected

    @staticmethod
    def test_old_format_fields():
        """旧字段名(url/title/original_content)应正确映射为 url/title/content。"""
        classified_content = [
            {
                "url": "https://example.com/b",
                "title": "旧格式标题",
                "original_content": "旧格式正文",
            }
        ]
        expected = {
            "search_record": [
                {"url": "https://example.com/b", "title": "旧格式标题", "content": "旧格式正文"}
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected

    @staticmethod
    def test_mixed_format_items():
        """新旧格式混合(不同项使用不同字段名)时，每个有效项都应被转换并保留顺序。"""
        classified_content = [
            {
                "doc_url": "https://example.com/a",
                "doc_title": "新格式标题",
                "passage_text": "新格式正文",
            },
            {
                "url": "https://example.com/b",
                "title": "旧格式标题",
                "original_content": "旧格式正文",
            },
        ]
        expected = {
            "search_record": [
                {"url": "https://example.com/a", "title": "新格式标题", "content": "新格式正文"},
                {"url": "https://example.com/b", "title": "旧格式标题", "content": "旧格式正文"},
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected

    @staticmethod
    def test_keeps_all_distinct_passages_under_same_url():
        """同一 URL 下不同段落内容均应保留,供来源匹配使用。

        passage-level 模式下同一 URL 会产生多个不同 passage_text 的段落,
        transform_search_record 不应按 URL 去重(否则会丢失来源匹配所需内容)。
        精确重复(同 url+title+content)由下游 preprocess_search_record 处理。
        """
        classified_content = [
            {
                "doc_url": "https://example.com/dup",
                "doc_title": "首次标题",
                "passage_text": "首次正文",
            },
            {
                "url": "https://example.com/dup",
                "title": "重复标题",
                "original_content": "重复正文",
            },
        ]
        expected = {
            "search_record": [
                {"url": "https://example.com/dup", "title": "首次标题", "content": "首次正文"},
                {"url": "https://example.com/dup", "title": "重复标题", "content": "重复正文"},
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected

    @staticmethod
    def test_empty_input_returns_empty_dict():
        """空输入(空列表或None)应返回空字典。"""
        assert SourceTracer.transform_search_record([]) == {}
        assert SourceTracer.transform_search_record(None) == {}

    @staticmethod
    def test_items_missing_required_fields_skipped():
        """缺少必需字段(url/title/content任一)的项应被跳过，仅保留有效项。"""
        classified_content = [
            {"url": "https://example.com/missing-content", "title": "无正文"},  # 缺 content
            {"url": "https://example.com/missing-title", "original_content": "无标题"},  # 缺 title
            {"title": "无URL", "original_content": "无URL正文"},  # 缺 url
            {"doc_url": "https://example.com/valid", "doc_title": "有效标题", "passage_text": "有效正文"},
        ]
        expected = {
            "search_record": [
                {"url": "https://example.com/valid", "title": "有效标题", "content": "有效正文"}
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected

    @staticmethod
    def test_non_dict_items_skipped():
        """非字典类型的项应被跳过，仅保留有效的字典项。"""
        classified_content = [
            "not a dict",
            123,
            None,
            ["a", "list"],
            {"url": "https://example.com/valid", "title": "有效标题", "original_content": "有效正文"},
        ]
        expected = {
            "search_record": [
                {"url": "https://example.com/valid", "title": "有效标题", "content": "有效正文"}
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected

    @staticmethod
    def test_all_items_invalid_returns_empty_record():
        """非空输入但全部项无效时，返回 {'search_record': []} 而非空字典。

        transform_search_record 仅在输入本身为假值(空列表/None)时返回 {}；
        非空输入即使没有有效项，也始终返回带 'search_record' 键的字典。
        """
        classified_content = [
            {"url": "https://example.com/missing-content", "title": "无正文"},  # 缺 content
            "not a dict",
            {"title": "无URL", "original_content": "无URL正文"},  # 缺 url
        ]
        result = SourceTracer.transform_search_record(classified_content)
        assert result == {"search_record": []}


class TestBuildCitationMappingPassageLevel:
    """_build_citation_mapping 新旧字段格式的测试用例。"""

    @staticmethod
    def test_passage_level_fields():
        """passage-level 字段(doc_title/doc_url/passage_text)应正确映射为 title/url/content。"""
        classified_content = [
            {
                "index": 1,
                "doc_title": "  深度  搜索  ",
                "doc_url": "https://example.com/p1",
                "passage_text": "这是第一段正文",
            },
            {
                "index": 2,
                "doc_title": "另一篇文章",
                "doc_url": "https://example.com/p2",
                "passage_text": "这是第二段正文",
            },
        ]
        result = _build_citation_mapping(classified_content)

        assert 1 in result
        assert 2 in result
        assert result[1]["title"] == "深度 搜索"  # _normalize_citation_title 合并空白
        assert result[1]["url"] == "https://example.com/p1"
        assert result[1]["content"] == "这是第一段正文"
        assert result[2]["title"] == "另一篇文章"
        assert result[2]["url"] == "https://example.com/p2"
        assert result[2]["content"] == "这是第二段正文"

    @staticmethod
    def test_old_fields_still_work():
        """旧字段名(title/url/original_content)仍应正常工作。"""
        classified_content = [
            {
                "index": 1,
                "title": "旧格式标题",
                "url": "https://example.com/old",
                "original_content": "旧格式正文",
            },
        ]
        result = _build_citation_mapping(classified_content)

        assert 1 in result
        assert result[1]["title"] == "旧格式标题"
        assert result[1]["url"] == "https://example.com/old"
        assert result[1]["content"] == "旧格式正文"

    @staticmethod
    def test_new_fields_take_precedence_when_both_present():
        """当新旧字段同时存在时，新字段优先（因为 or 逻辑：旧字段为空串时回退到新字段）。"""
        classified_content = [
            {
                "index": 1,
                "title": "",
                "url": "",
                "original_content": "",
                "doc_title": "新格式标题",
                "doc_url": "https://example.com/new",
                "passage_text": "新格式正文",
            },
        ]
        result = _build_citation_mapping(classified_content)

        assert 1 in result
        assert result[1]["title"] == "新格式标题"
        assert result[1]["url"] == "https://example.com/new"
        assert result[1]["content"] == "新格式正文"

    @staticmethod
    def test_same_index_merges_content():
        """同一 index 的多条记录应合并 content。"""
        classified_content = [
            {
                "index": 1,
                "doc_title": "合并标题",
                "doc_url": "https://example.com/merge",
                "passage_text": "第一段",
            },
            {
                "index": 1,
                "doc_title": "合并标题",
                "doc_url": "https://example.com/merge",
                "passage_text": "第二段",
            },
        ]
        result = _build_citation_mapping(classified_content)

        assert 1 in result
        assert result[1]["content"] == "第一段第二段"

    @staticmethod
    def test_index_zero_skipped():
        """index 为 0 或缺失的条目应被跳过。"""
        classified_content = [
            {"index": 0, "doc_title": "零索引", "doc_url": "https://example.com/0", "passage_text": "应跳过"},
            {"doc_title": "无索引", "doc_url": "https://example.com/no", "passage_text": "应跳过"},
            {"index": 1, "doc_title": "有效", "doc_url": "https://example.com/1", "passage_text": "有效内容"},
        ]
        result = _build_citation_mapping(classified_content)

        assert 0 not in result
        assert 1 in result
        assert result[1]["title"] == "有效"
