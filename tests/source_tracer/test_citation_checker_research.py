import json
import re
from collections import OrderedDict
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research import CitationCheckerResearch
from openjiuwen_deepsearch.common.exception import CustomIndexException
from openjiuwen_deepsearch.common.status_code import StatusCode


class MockMatch:
    """模拟 re.Match 对象，用于测试"""

    def __init__(self, start, end, string="mocked string"):
        self._start = start
        self._end = end
        self.string = string

    def start(self):
        return self._start

    def end(self):
        return self._end


class TestResearchCitationChecker:
    """Test cases for CitationCheckerResearch core functionality."""

    def test_init(self):
        """Test CitationCheckerResearch initialization."""
        checker = CitationCheckerResearch("mock_model")
        assert hasattr(checker, 'citation_verifier')
        assert checker.citation_verifier is not None

    # Test core static methods that contain important business logic
    def test_validate_url_match_exact_match(self):
        """Test URL validation with exact match."""
        url = "https://example.com"
        datas = [{'url': url, 'valid': True}]
        citation_index = 0
        checker = CitationCheckerResearch("mock_model")
        result_url, is_valid = checker.validate_url_match(
            url, datas, citation_index)

        assert result_url == url
        assert is_valid is True

    @patch('openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research.are_similar_urls')
    @patch('openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research.LogManager')
    def test_validate_url_match_mismatch_with_logging(self, mock_log_manager, mock_are_similar_urls):
        """Test URL validation with mismatch and logging."""
        mock_log_manager.is_sensitive.return_value = False
        mock_are_similar_urls.return_value = False
        url = "https://example.com"
        datas = [{'url': "https://different.com", 'valid': True}]
        citation_index = 0
        checker = CitationCheckerResearch("mock_model")
        result_url, is_valid = checker.validate_url_match(
            url, datas, citation_index)

        assert result_url == url
        assert is_valid is False

    def test_handle_duplicate_citations_keep_higher_score(self):
        """Test handling duplicate URLs with higher score."""
        url = "https://example.com"
        # 创建模拟的match对象，使它们相邻
        old_match = MockMatch(10, 50)
        new_match = MockMatch(52, 92)  # 与旧引用相邻（相差2个字符以内）

        current_data = {'score': 0.9, 'match': new_match}
        existing_data = {'score': 0.8, 'match': old_match}
        datas = [existing_data, current_data]
        processed_citation_urls = {url: {'score': 0.8, 'data_index': 0}}
        citation_index = 1
        checker = CitationCheckerResearch("mock_model")

        result = checker.remove_duplicate_citations(
            url, datas, processed_citation_urls, citation_index)

        assert processed_citation_urls[url]['score'] == 0.9  # Update to higher score
        assert processed_citation_urls[url]['data_index'] == 1  # Update to new index
        assert datas[0]["valid"] is False
        assert datas[0]["invalid_reason"] == "score lower than another citation"
        assert 0 in result

    def test_handle_duplicate_citations_keep_existing_score(self):
        """Test handling duplicate URLs with lower score."""
        url = "https://example.com"
        # 创建模拟的match对象，使它们相邻
        old_match = MockMatch(10, 50)
        new_match = MockMatch(52, 92)  # 与旧引用相邻（相差2个字符以内）

        current_data = {'score': 0.6, 'match': new_match}
        existing_data = {'score': 0.8, 'match': old_match}
        datas = [existing_data, current_data]
        processed_citation_urls = {url: {'score': 0.8, 'data_index': 0}}
        citation_index = 1
        checker = CitationCheckerResearch("mock_model")

        result = checker.remove_duplicate_citations(
            url, datas, processed_citation_urls, citation_index)

        assert processed_citation_urls[url]['score'] == 0.8  # Keep existing higher score
        assert processed_citation_urls[url]['data_index'] == 0  # Keep existing index
        assert datas[1]["valid"] is False
        assert datas[1]["invalid_reason"] == "score lower than another citation"
        assert 1 in result

    def test_handle_duplicate_citations_non_adjacent(self):
        """Test handling non-adjacent duplicate URLs."""
        url = "https://example.com"
        # 创建模拟的match对象，使它们不相邻
        old_match = MockMatch(10, 50)
        new_match = MockMatch(100, 140)  # 与旧引用不相邻（相差超过2个字符）

        current_data = {'score': 0.9, 'match': new_match}
        existing_data = {'score': 0.8, 'match': old_match}
        datas = [existing_data, current_data]
        processed_citation_urls = {url: {'score': 0.8, 'data_index': 0}}
        citation_index = 1
        checker = CitationCheckerResearch("mock_model")

        result = checker.remove_duplicate_citations(
            url, datas, processed_citation_urls, citation_index)

        # 不相邻的引用应该更新seen_urls但不删除任何引用
        assert processed_citation_urls[url]['score'] == 0.9  # Update to higher score
        assert processed_citation_urls[url]['data_index'] == 1  # Update to new index
        assert datas[0].get("valid", True)  # 保持有效状态
        assert datas[1].get("valid", True)  # 保持有效状态
        assert len(result) == 0  # 没有要删除的索引

    def test_save_citation_message_filter_invalid_and_images(self):
        """Test filtering out invalid and image citations."""
        datas = [
            {
                'url': 'https://example.com',
                'title': 'Example',
                'valid': True
            },
            {
                'url': 'https://invalid.com',
                'title': 'Invalid',
                'valid': False
            },
            {
                'url': 'https://image.com',
                'title': 'Image',
                'valid': True,
                'is_image': True
            }
        ]

        result = CitationCheckerResearch.organize_citations_for_frontend(datas)

        assert result['code'] == 0
        assert len(result['data']) == 1  # Only valid, non-image citation
        assert result['data'][0]['url'] == "https://example.com"
        assert result['data'][0]['from'] == 'web'

    def test_save_citation_message_local_source(self):
        """Test handling local source citations."""
        datas = [
            {
                'url': 'local/path/file.pdf',  # Non-http URL for local source
                'title': 'Local',
                'valid': True,
                'is_image': False
            }
        ]

        result = CitationCheckerResearch.organize_citations_for_frontend(datas)

        assert result['code'] == 0
        assert len(result['data']) == 1
        assert result['data'][0]['from'] == 'local'

    # Test core instance methods
    def setup_method(self):
        """Set up test fixtures."""
        self.checker = CitationCheckerResearch("mock_model")

    @patch('openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research.LogManager')
    def test_process_single_citation_with_logging(self, mock_log_manager):
        """Test processing a single citation with logging enabled."""
        mock_log_manager.is_sensitive.return_value = False
        para = "这是一个测试[source_tracer_result][示例](https://example.com)引用。"
        pattern = r'\[source_tracer_result\](!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))'
        match = re.search(pattern, para)
        datas = [{'url': 'https://example.com', 'valid': True, 'score': 0.8}]
        processed_citation_urls = {}
        data_index = 0

        result_del_indices = self.checker.validate_and_process_single_citation(
            match, datas, processed_citation_urls, data_index)

        assert result_del_indices == []
        assert datas[0]['is_image'] is False
        assert 'match' in datas[0]

    def test_process_single_citation_invalid_data(self):
        """Test processing a citation with invalid data."""
        para = "这是一个测试[source_tracer_result][示例](https://example.com)引用。"
        pattern = r'\[source_tracer_result\](!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))'
        match = re.search(pattern, para)
        datas = [{'url': 'https://example.com', 'valid': False}]
        processed_citation_urls = {}
        data_index = 0

        result_del_indices = self.checker.validate_and_process_single_citation(
            match, datas, processed_citation_urls, data_index)

        assert 0 in result_del_indices
        assert datas[0]['is_image'] is False

    def test_process_single_citation_invalid_url(self):
        """Test processing a citation with invalid URL."""
        para = "这是一个测试[source_tracer_result][示例](https://example.com)引用。"
        pattern = r'\[source_tracer_result\](!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))'
        match = re.search(pattern, para)
        datas = [{'url': 'https://different.com', 'valid': True}]
        processed_citation_urls = {}
        data_index = 0

        result_del_indices = self.checker.validate_and_process_single_citation(
            match, datas, processed_citation_urls, data_index)

        assert 0 in result_del_indices
        assert datas[0]['valid'] is False

    def test_process_single_citation_index_out_of_range(self):
        """Test processing a citation with index out of range."""
        para = "这是一个测试[source_tracer_result][示例](https://example.com)引用。"
        pattern = r'\[source_tracer_result\](!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))'
        match = re.search(pattern, para)
        datas = []  # Empty datas
        processed_citation_urls = {}
        data_index = 0

        with pytest.raises(CustomIndexException):
            self.checker.validate_and_process_single_citation(
                match, datas, processed_citation_urls, data_index)

    def test_process_single_paragraph_length_mismatch(self):
        """Test processing a paragraph with citation length mismatch."""
        para = "这是一个测试[source_tracer_result][示例](https://example.com)引用。"
        datas = []
        data_index = 0

        # Mock to simulate that validate_and_process_single_citation raises an exception
        with patch.object(self.checker, 'validate_and_process_single_citation') as mock_process:
            mock_process.side_effect = CustomIndexException(
                StatusCode.PARAM_CHECK_ERROR_INDEX_OUT_OF_RANGE.code,
                "Index out of range"
            )

            with pytest.raises(CustomIndexException):
                self.checker.process_single_paragraph_citations(
                    para, datas, data_index)

    @patch('openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research.LogManager')
    def test_preprocess_text_and_datas_with_logging(self, mock_log_manager):
        """Test preprocessing with logging enabled."""
        mock_log_manager.is_sensitive.return_value = False
        text = {'article': "这是一个测试文章"}
        datas = [{'url': 'https://example.com', 'valid': True}]

        result_text, result_datas = self.checker.preprocess_text_and_citations(
            text, datas)

        assert result_text == "这是一个测试文章"
        assert result_datas == datas

    def test_replace_inline_citations_with_image(self):
        """Test replacement with image citation."""
        markdown_text = "这是一个测试![source_tracer_result][图片](https://image.com)引用。"
        datas = [{'url': 'https://image.com', 'valid': True}]
        inline_ref_pattern = re.compile(
            r'\[source_tracer_result\](?:!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))')

        result_text, references, result_datas = self.checker.replace_inline_citations(
            markdown_text, datas, inline_ref_pattern)

        # Should contain image citation format (title might be None if not in datas)
        assert '![[' in result_text and 'https://image.com' in result_text
        assert references == OrderedDict()  # No references for images

    @patch('openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research.LogManager')
    def test_transform_references_with_logging(self, mock_log_manager):
        """Test transform references with logging enabled."""
        mock_log_manager.is_sensitive.return_value = False
        text = {'article': "这是一个测试文章"}
        datas = [{'url': 'https://example.com', 'valid': True}]

        result_text, result_datas = self.checker.transform_references(
            text, datas)

        assert isinstance(result_text, str)
        # datas might be filtered out during processing if no citations found
        assert isinstance(result_datas, list)

    # Test main workflow
    @pytest.mark.asyncio
    async def test_checker_success(self):
        """Test successful execution of main checker method."""
        text = {'article': "这是一个测试文章"}
        datas = [{'url': 'https://example.com', 'valid': True}]

        with patch.object(self.checker.citation_verifier, 'run', new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = datas

            result = await self.checker.checker(text, datas)
            result_dict = json.loads(result)

            assert 'checked_trace_source_report_content' in result_dict
            assert 'citation_messages' in result_dict
            assert result_dict['citation_messages']['code'] == 0
            mock_verify.assert_called_once_with(datas)

    @pytest.mark.asyncio
    @patch('openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research.LogManager')
    async def test_checker_with_detailed_logging(self, mock_log_manager):
        """Test checker method with detailed logging enabled."""
        mock_log_manager.is_sensitive.return_value = False
        text = {'article': "这是一个测试文章"}
        datas = [{'url': 'https://example.com', 'valid': True}]

        with patch.object(self.checker.citation_verifier, 'run', new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = datas

            result = await self.checker.checker(text, datas)
            result_dict = json.loads(result)

            assert 'checked_trace_source_report_content' in result_dict
            assert 'citation_messages' in result_dict
            assert result_dict['citation_messages']['code'] == 0

    @pytest.mark.asyncio
    async def test_checker_with_verification_exception(self):
        """Test checker method with verification exception."""
        text = {'article': "这是一个测试文章"}
        datas = [{'url': 'https://example.com', 'valid': True}]

        with patch.object(self.checker.citation_verifier, 'run', new_callable=AsyncMock) as mock_verify:
            mock_verify.side_effect = Exception("验证错误")

            with pytest.raises(Exception, match="验证错误"):
                await self.checker.checker(text, datas)

    # Test integration scenario
    @pytest.mark.asyncio
    async def test_end_to_end_citation_processing(self):
        """Test end-to-end citation processing with realistic data."""
        text = {
            'article': "这是一个测试文章，包含[source_tracer_result][示例1](https://example1.com)和[source_tracer_result][示例2](https://example2.com)引用。"}
        datas = [
            {'url': 'https://example1.com', 'title': '示例1',
             'valid': True, 'score': 0.8},
            {'url': 'https://example2.com', 'title': '示例2',
             'valid': True, 'score': 0.9}
        ]

        with patch.object(self.checker.citation_verifier, 'run', new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = datas

            result = await self.checker.checker(text, datas)
            result_dict = json.loads(result)

            # Verify the result structure
            assert 'checked_trace_source_report_content' in result_dict
            assert 'citation_messages' in result_dict
            assert result_dict['citation_messages']['code'] == 0
            assert len(result_dict['citation_messages']['data']) == 2

            # Verify citations are properly processed
            response_content = result_dict['checked_trace_source_report_content']
            assert '[[1]]' in response_content
            assert '[[2]]' in response_content
            assert '[1]. [示例1]' in response_content
            assert '[2]. [示例2]' in response_content
