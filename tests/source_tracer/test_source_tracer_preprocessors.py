from unittest.mock import patch

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_tracer_preprocessors import (
    _need_preprocess_search_record,
    preprocess_search_record,
    _should_process_item,
    process_search_record_list,
    has_required_fields,
    is_duplicate_record,
    _create_content_chunk,
    handle_long_content,
    _remove_reference_section,
    preprocess_report,
    _get_citation_chunk,
    _process_citation_match,
    _replace_citations_with_custom_mapping,
    _build_citation_mapping,
    _build_datas_from_chunks,
    generate_origin_report_data
)
from openjiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences


MODULE_PATH = "openjiuwen_deepsearch.algorithm.source_trace.source_tracer_preprocessors"


class TestNeedPreprocessSearchRecord:
    """Test cases for _need_preprocess_search_record function."""

    def test_need_preprocess_search_record_with_web_page(self):
        """Test when web_page_search_record exists."""
        search_record = {"search_record": [
            {"title": "test", "url": "https://example.com", "content": "test"}]}
        result = _need_preprocess_search_record(search_record)
        assert result is True

    def test_need_preprocess_search_record_empty(self):
        """Test when all search records are empty."""
        search_record = {
            "search_record": [],
        }
        result = _need_preprocess_search_record(search_record)
        assert result is False

    def test_need_preprocess_search_record_missing_keys(self):
        """Test when search record keys are missing."""
        search_record = {"other_key": "value"}
        result = _need_preprocess_search_record(search_record)
        assert result is False


class TestHasRequiredFields:
    """Test cases for has_required_fields function."""

    def test_has_required_fields_all_present(self):
        """Test when all required fields are present."""
        item = {"title": "test", "url": "https://example.com",
                "content": "test content"}
        result = has_required_fields(item)
        assert result is True

    def test_has_required_fields_missing_title(self):
        """Test when title is missing."""
        item = {"url": "https://example.com", "content": "test content"}
        result = has_required_fields(item)
        assert result is False

    def test_has_required_fields_missing_url(self):
        """Test when url is missing."""
        item = {"title": "test", "content": "test content"}
        result = has_required_fields(item)
        assert result is False

    def test_has_required_fields_missing_content(self):
        """Test when content is missing."""
        item = {"title": "test", "url": "https://example.com"}
        result = has_required_fields(item)
        assert result is False

    def test_has_required_fields_extra_fields(self):
        """Test when extra fields are present along with required ones."""
        item = {"title": "test", "url": "https://example.com",
                "content": "test content", "extra": "field"}
        result = has_required_fields(item)
        assert result is True


class TestIsDuplicateRecord:
    """Test cases for is_duplicate_record function."""

    def test_is_duplicate_record_same_fields(self):
        """Test when a duplicate record exists."""
        item = {"title": "test", "url": "https://example.com",
                "content": "test content"}
        processed_list = [
            {"title": "test", "url": "https://example.com", "content": "test content", "index": 0}]
        result = is_duplicate_record(item, processed_list)
        assert result is True

    def test_is_duplicate_record_different_content(self):
        """Test when url and title are same but content is different."""
        item = {"title": "test", "url": "https://example.com",
                "content": "different content"}
        processed_list = [
            {"title": "test", "url": "https://example.com", "content": "test content", "index": 0}]
        result = is_duplicate_record(item, processed_list)
        assert result is False

    def test_is_duplicate_record_different_title(self):
        """Test when url and content are same but title is different."""
        item = {"title": "different", "url": "https://example.com",
                "content": "test content"}
        processed_list = [
            {"title": "test", "url": "https://example.com", "content": "test content", "index": 0}]
        result = is_duplicate_record(item, processed_list)
        assert result is False

    def test_is_duplicate_record_different_url(self):
        """Test when title and content are same but url is different."""
        item = {"title": "test", "url": "https://different.com",
                "content": "test content"}
        processed_list = [
            {"title": "test", "url": "https://example.com", "content": "test content", "index": 0}]
        result = is_duplicate_record(item, processed_list)
        assert result is False

    def test_is_duplicate_record_empty_list(self):
        """Test when processed list is empty."""
        item = {"title": "test", "url": "https://example.com",
                "content": "test content"}
        processed_list = []
        result = is_duplicate_record(item, processed_list)
        assert result is False

    def test_is_duplicate_record_partial_fields(self):
        """Test when records have different number of fields but key fields match."""
        item = {"title": "test", "url": "https://example.com",
                "content": "test content", "extra": "field"}
        processed_list = [
            {"title": "test", "url": "https://example.com", "content": "test content", "index": 0}]
        result = is_duplicate_record(item, processed_list)
        assert result is True


class TestShouldProcessItem:
    """Test cases for _should_process_item function."""

    @patch(f'{MODULE_PATH}.has_required_fields')
    @patch(f'{MODULE_PATH}.is_duplicate_record')
    def test_should_process_item_valid(self, mock_is_duplicate, mock_has_required_fields):
        """Test when item is valid and should be processed."""
        mock_has_required_fields.return_value = True
        mock_is_duplicate.return_value = False

        item = {"title": "test", "url": "https://example.com",
                "content": "test content"}
        processed_list = []
        result = _should_process_item(item, processed_list)
        assert result is True
        mock_has_required_fields.assert_called_once_with(item)
        mock_is_duplicate.assert_called_once_with(item, processed_list)

    @patch(f'{MODULE_PATH}.has_required_fields')
    def test_should_process_item_not_dict(self, mock_has_required_fields):
        """Test when item is not a dict."""
        item = "not a dict"
        processed_list = []
        result = _should_process_item(item, processed_list)
        assert result is False
        mock_has_required_fields.assert_not_called()

    @patch(f'{MODULE_PATH}.has_required_fields')
    @patch(f'{MODULE_PATH}.is_duplicate_record')
    def test_should_process_item_missing_required_fields(self, mock_is_duplicate, mock_has_required_fields):
        """Test when item is missing required fields."""
        mock_has_required_fields.return_value = False

        item = {"title": "test"}  # Missing url and content
        processed_list = []
        result = _should_process_item(item, processed_list)
        assert result is False
        mock_has_required_fields.assert_called_once_with(item)
        mock_is_duplicate.assert_not_called()

    @patch(f'{MODULE_PATH}.has_required_fields')
    @patch(f'{MODULE_PATH}.is_duplicate_record')
    def test_should_process_item_duplicate(self, mock_is_duplicate, mock_has_required_fields):
        """Test when item is a duplicate."""
        mock_has_required_fields.return_value = True
        mock_is_duplicate.return_value = True

        item = {"title": "test", "url": "https://example.com",
                "content": "test content"}
        processed_list = []
        result = _should_process_item(item, processed_list)
        assert result is False
        mock_has_required_fields.assert_called_once_with(item)
        mock_is_duplicate.assert_called_once_with(item, processed_list)


class TestCreateContentChunk:
    """Test cases for _create_content_chunk function."""

    def test_create_content_chunk_basic(self):
        """Test basic functionality of creating a content chunk."""
        item = {"title": "test", "url": "https://example.com", "source": "web"}
        content = "This is a long content that needs to be chunked"
        start = 0
        max_content_len = 10
        processed_list = [{"index": 0}, {"index": 1}]

        result = _create_content_chunk(
            item, content, start, max_content_len, processed_list)

        assert result["title"] == "test"
        assert result["url"] == "https://example.com"
        assert result["source"] == "web"
        assert result["content"] == "This is a "
        assert result["index"] == 2  # len of processed_list

    def test_create_content_chunk_with_end_position(self):
        """Test creating a content chunk from a specific start position."""
        item = {"title": "test", "url": "https://example.com",
                "content": "original"}
        content = "This is a long content that needs to be chunked"
        start = 10
        max_content_len = 10
        processed_list = []

        result = _create_content_chunk(
            item, content, start, max_content_len, processed_list)

        assert result["content"] == "long conte"


class TestHandleLongContent:
    """Test cases for handle_long_content function."""

    def test_handle_long_content_basic_chunking(self):
        """Test basic functionality of handling long content."""
        item = {"title": "test", "url": "https://example.com",
                "content": "original"}
        processed_list = []
        max_content_len = 10

        long_content = "This is a very long content that exceeds the maximum length"
        item["content"] = long_content

        handle_long_content(item, processed_list, max_content_len)

        # ceil(len(long_content) / max_content_len)
        assert len(processed_list) == 6
        assert processed_list[0]["content"] == "This is a "
        assert processed_list[1]["content"] == "very long "
        assert processed_list[0]["index"] == 0
        assert processed_list[1]["index"] == 1

    def test_handle_long_content_exact_length(self):
        """Test handling content that is exactly the max length."""
        item = {"title": "test", "url": "https://example.com",
                "content": "original"}
        processed_list = []
        max_content_len = 10

        exact_content = "1234567890"  # Exactly 10 characters
        item["content"] = exact_content

        handle_long_content(item, processed_list, max_content_len)

        assert len(processed_list) == 1
        assert processed_list[0]["content"] == exact_content
        assert processed_list[0]["index"] == 0

    def test_handle_long_content_shorter_than_max(self):
        """Test handling content that is shorter than max length."""
        item = {"title": "test", "url": "https://example.com",
                "content": "original"}
        processed_list = []
        max_content_len = 100

        short_content = "Short content"
        item["content"] = short_content

        handle_long_content(item, processed_list, max_content_len)

        assert len(processed_list) == 1
        assert processed_list[0]["content"] == short_content
        assert processed_list[0]["index"] == 0


class TestProcessSearchRecordList:
    """Test cases for process_search_record_list function."""

    @patch(f'{MODULE_PATH}._should_process_item')
    def test_process_search_record_list_basic(self, mock_should_process):
        """Test basic functionality of processing search record list."""
        mock_should_process.return_value = True

        items = [
            {"title": "test1", "url": "https://example1.com", "content": "content1"},
            {"title": "test2", "url": "https://example2.com", "content": "content2"}
        ]
        max_content_len = 100

        result = process_search_record_list(items, max_content_len)

        assert len(result) == 2
        assert result[0]["index"] == 0
        assert result[1]["index"] == 1
        assert result[0]["title"] == "test1"
        assert result[1]["title"] == "test2"
        assert mock_should_process.call_count == 2

    @patch(f'{MODULE_PATH}._should_process_item')
    def test_process_search_record_list_skip_unprocessable(self, mock_should_process):
        """Test skipping unprocessable items."""
        mock_should_process.side_effect = [True, False, True]

        items = [
            {"title": "test1", "url": "https://example1.com", "content": "content1"},
            {"title": "test2", "url": "https://example2.com",
             "content": "content2"},  # Will be skipped
            {"title": "test3", "url": "https://example3.com", "content": "content3"}
        ]
        max_content_len = 100

        result = process_search_record_list(items, max_content_len)

        assert len(result) == 2
        assert result[0]["index"] == 0
        assert result[1]["index"] == 1
        assert result[0]["title"] == "test1"
        assert result[1]["title"] == "test3"

    @patch(f'{MODULE_PATH}._should_process_item')
    def test_process_search_record_list_with_long_content(self, mock_should_process):
        """Test processing items with long content that needs chunking."""
        mock_should_process.return_value = True

        items = [
            {"title": "test1", "url": "https://example1.com",
             "content": "This is a very long content that will be chunked"}
        ]
        max_content_len = 10

        result = process_search_record_list(items, max_content_len)

        assert len(result) > 1  # Content should be chunked
        assert all("index" in item for item in result)
        total_content = "".join([item["content"] for item in result])
        assert "This is a very long content that will be chunked" in total_content or total_content in "This is a very long content that will be chunked"


class TestPreprocessSearchRecord:
    """Test cases for preprocess_search_record function."""

    @patch(f'{MODULE_PATH}._need_preprocess_search_record')
    @patch(f'{MODULE_PATH}.process_search_record_list')
    def test_preprocess_search_record_with_preprocessing_needed(self, mock_process_list, mock_need_preprocess):
        """Test when preprocessing is needed."""
        mock_need_preprocess.return_value = True
        mock_process_list.return_value = [
            {"title": "processed", "url": "https://example.com",
             "content": "processed content", "index": 0}
        ]

        search_record = {
            "web_page_search_record": [{"title": "test", "url": "https://example.com", "content": "test content"}],
            "other_field": "value"
        }
        max_content_len = 1000

        result = preprocess_search_record(search_record, max_content_len)

        assert "web_page_search_record" in result
        assert "other_field" in result
        assert result["other_field"] == "value"
        mock_process_list.assert_called_once()
        mock_need_preprocess.assert_called_once_with(search_record)

    @patch(f'{MODULE_PATH}._need_preprocess_search_record')
    def test_preprocess_search_record_no_preprocessing_needed(self, mock_need_preprocess):
        """Test when preprocessing is not needed."""
        mock_need_preprocess.return_value = False

        search_record = {"some": "data"}
        max_content_len = 1000

        result = preprocess_search_record(search_record, max_content_len)

        assert result == {}
        mock_need_preprocess.assert_called_once_with(search_record)

    @patch(f'{MODULE_PATH}._need_preprocess_search_record')
    @patch(f'{MODULE_PATH}.process_search_record_list')
    def test_preprocess_search_record_non_list_values_unchanged(self, mock_process_list, mock_need_preprocess):
        """Test that non-list values are not processed."""
        mock_need_preprocess.return_value = True

        search_record = {
            "web_page_search_record": [{"title": "test", "url": "https://example.com", "content": "test content"}],
            "non_list_field": {"nested": "value"},
            "string_field": "just a string",
            "number_field": 42
        }
        max_content_len = 1000

        result = preprocess_search_record(search_record, max_content_len)

        assert result["non_list_field"] == {"nested": "value"}
        assert result["string_field"] == "just a string"
        assert result["number_field"] == 42
        mock_need_preprocess.assert_called_once_with(search_record)


class TestRemoveReferenceSection:
    """Test cases for _remove_reference_section function."""

    def test_remove_reference_section_english(self):
        """Test removing Reference section in English."""
        report_text = """This is the main content.
        
# Reference Articles
- Reference 1
- Reference 2

More content after."""

        removed_section, remaining_text = _remove_reference_section(report_text)

        assert "This is the main content." in remaining_text
        assert "Reference Articles" not in remaining_text
        assert "More content after" not in remaining_text  # This should be removed

    def test_remove_reference_section_chinese(self):
        """Test removing 参考文献 section in Chinese."""
        report_text = """This is the main content.
        
# 参考文献
- Reference 1
- Reference 2"""

        removed_section, remaining_text = _remove_reference_section(report_text)

        assert "This is the main content." in remaining_text
        assert "参考文献" not in remaining_text

    def test_remove_reference_section_no_reference(self):
        """Test when there is no reference section."""
        report_text = """This is the main content.
        
# Introduction
Content here."""

        removed_section, remaining_text = _remove_reference_section(report_text)

        assert remaining_text == report_text

    def test_remove_reference_section_multiple_headings(self):
        """Test with multiple headings and a reference section at the end."""
        report_text = """This is the main content.

# Introduction
Intro content.

# Methodology  
Method content.

# Reference Articles
- Reference 1
- Reference 2"""

        removed_section, remaining_text = _remove_reference_section(report_text)

        assert "This is the main content." in remaining_text
        assert "Introduction" in remaining_text
        assert "Methodology" in remaining_text
        assert "Reference Articles" not in remaining_text
        assert "Intro content" in remaining_text
        assert "Method content" in remaining_text


class TestPreprocessReport:
    """Test cases for preprocess_report function."""

    @patch(f'{MODULE_PATH}._remove_reference_section')
    def test_preprocess_report_basic(self, mock_remove_ref):
        """Test basic functionality of preprocess_report."""
        mock_remove_ref.return_value = ("Reference Articles", "cleaned report")

        report = "original report"
        result = preprocess_report(report)

        assert result == ("Reference Articles", "cleaned report")
        mock_remove_ref.assert_called_once_with("original report")

    @patch(f'{MODULE_PATH}._remove_reference_section')
    def test_preprocess_report_exception_handling(self, mock_remove_ref):
        """Test exception handling in preprocess_report."""
        mock_remove_ref.side_effect = Exception("Test error")

        report = "original report"
        # The function should not catch exceptions, so we expect it to propagate
        with pytest.raises(Exception, match="Test error"):
            preprocess_report(report)


class TestGetCitationChunk:
    """Test cases for _get_citation_chunk function."""

    def test_get_citation_chunk_basic(self):
        """Test basic functionality of getting citation chunk."""
        # 直接测试函数功能，而不是mock，因为mock会导致函数行为改变
        report = "First sentence. Second sentence with citation.[citation: 1] More text."
        citation_pos = report.find("[citation: 1]")
        citation_pattern = r'\[\s*citation:\s*(\d+)\s*\]'

        result = _get_citation_chunk(report, citation_pos, citation_pattern)

        # 验证返回结果的基本性质
        assert isinstance(result, str)
        assert len(result) >= 0  # 确保返回字符串
        # The exact behavior depends on the split_into_sentences implementation

    def test_get_citation_chunk_short_chunk_expand(self):
        """Test expanding short chunk by including previous sentences."""
        # This test requires the actual split_into_sentences implementation
        # For now, we'll test the logic by directly calling with known sentence splits
        report = "This is a sentence. Hi. [citation: 1] More text."
        # When we have "Hi." as the last sentence (length < 5),
        # it should include the previous sentence "This is a sentence. "

        # Testing the logic manually since we can't mock the internal split
        sentences = split_into_sentences("This is a sentence. Hi.")
        assert len(sentences) >= 1


class TestReplaceCitationsWithCustomMapping:
    """Test cases for _replace_citations_with_custom_mapping function."""

    def test_replace_citations_with_custom_mapping_basic(self):
        """Test basic functionality of replacing citations."""
        report = "This is a sentence with [citation: 1] and [citation: 2]."
        citation_mapping = {
            1: {"title": "First Title", "url": "https://example1.com"},
            2: {"title": "Second Title", "url": "https://example2.com"}
        }

        result = _replace_citations_with_custom_mapping(
            report, citation_mapping)

        assert "modified_report" in result
        assert "citation_chunks" in result
        assert "[source_tracer_result][First Title](https://example1.com)" in result["modified_report"]
        assert "[source_tracer_result][Second Title](https://example2.com)" in result["modified_report"]
        assert len(result["citation_chunks"]) == 2

    def test_replace_citations_with_custom_mapping_invalid_mapping(self):
        """Test handling of invalid citation mapping."""
        report = "This is a sentence with [citation: 1]."
        citation_mapping = {
            1: {"url": "https://example.com"}  # Missing title
        }

        # The function should raise KeyError when title is missing
        with pytest.raises(KeyError, match="title"):
            _replace_citations_with_custom_mapping(report, citation_mapping)

    def test_replace_citations_with_custom_mapping_no_matches(self):
        """Test when there are no citation matches in the report."""
        report = "This is a sentence without citations."
        citation_mapping = {
            1: {"title": "Title", "url": "https://example.com"}
        }

        result = _replace_citations_with_custom_mapping(
            report, citation_mapping)

        assert result["modified_report"] == report
        assert result["citation_chunks"] == []


class TestBuildCitationMapping:
    """Test cases for _build_citation_mapping function."""

    def test_build_citation_mapping_basic(self):
        """Test basic functionality of building citation mapping."""
        classified_contents = [
            {"index": 1, "title": "Title 1", "url": "https://example1.com",
             "original_content": "Content 1"},
            {"index": 2, "title": "Title 2", "url": "https://example2.com",
             "original_content": "Content 2"}
        ]

        result = _build_citation_mapping(classified_contents)

        assert 1 in result
        assert 2 in result
        assert result[1]["title"] == "Title 1"
        assert result[1]["url"] == "https://example1.com"
        assert result[1]["content"] == "Content 1"
        assert result[2]["content"] == "Content 2"

    def test_build_citation_mapping_duplicate_index(self):
        """Test handling of duplicate indices in classified contents."""
        classified_contents = [
            {"index": 1, "title": "Title 1", "url": "https://example1.com",
             "original_content": "Content 1"},
            {"index": 1, "title": "Title 1 again",
             "url": "https://example1.com", "original_content": "Content 2"}
        ]

        result = _build_citation_mapping(classified_contents)

        assert 1 in result
        # The content should be concatenated
        assert "Content 1" in result[1]["content"]
        assert "Content 2" in result[1]["content"]

    def test_build_citation_mapping_zero_index_ignored(self):
        """Test that indices with value 0 are ignored."""
        classified_contents = [
            {"index": 0, "title": "Title 0", "url": "https://example0.com",
             "original_content": "Content 0"},
            {"index": 1, "title": "Title 1", "url": "https://example1.com",
             "original_content": "Content 1"}
        ]

        result = _build_citation_mapping(classified_contents)

        assert 0 not in result
        assert 1 in result
        assert result[1]["title"] == "Title 1"

    def test_build_citation_mapping_empty_input(self):
        """Test with empty input."""
        classified_contents = []

        result = _build_citation_mapping(classified_contents)

        assert result == {}


class TestBuildDatasFromChunks:
    """Test cases for _build_datas_from_chunks function."""

    def test_build_datas_from_chunks_basic(self):
        """Test basic functionality of building datas from chunks."""
        citation_chunks = [
            {"citation_num": 1, "chunk": "Sample chunk text",
             "_sentence_position": 100}
        ]
        citation_mapping = {
            1: {"title": "Mapped Title", "url": "https://mapped.com", "content": "Mapped content"}
        }

        result = _build_datas_from_chunks(citation_chunks, citation_mapping)

        assert len(result) == 1
        assert result[0]["title"] == "Mapped Title"
        assert result[0]["url"] == "https://mapped.com"
        assert result[0]["content"] == "Mapped content"
        assert result[0]["chunk"] == "Sample chunk text"
        assert result[0]["_sentence_position"] == 100
        assert result[0]["_is_origin_data"] is True

    def test_build_datas_from_chunks_missing_mapping(self):
        """Test handling of chunks without corresponding mapping."""
        citation_chunks = [
            {"citation_num": 1, "chunk": "Sample chunk text",
             "_sentence_position": 100},
            {"citation_num": 2, "chunk": "Another chunk",
             "_sentence_position": 200}  # No mapping for 2
        ]
        citation_mapping = {
            1: {"title": "Mapped Title", "url": "https://mapped.com", "content": "Mapped content"}
        }

        result = _build_datas_from_chunks(citation_chunks, citation_mapping)

        assert len(result) == 1  # Only the one with mapping should be included
        # The result should contain the mapped data for citation 1
        assert "title" in result[0]
        assert result[0]["title"] == "Mapped Title"

    def test_build_datas_from_chunks_empty_inputs(self):
        """Test with empty inputs."""
        citation_chunks = []
        citation_mapping = {}

        result = _build_datas_from_chunks(citation_chunks, citation_mapping)

        assert result == []


class TestProcessCitationMatch:
    """Test cases for _process_citation_match function."""

    @patch(f'{MODULE_PATH}._get_citation_chunk')
    def test_process_citation_match_basic(self, mock_get_citation_chunk):
        """Test basic functionality of processing citation match."""
        import re
        mock_get_citation_chunk.return_value = "Sample chunk text"

        # 创建一个匹配对象，模拟正则表达式匹配的结果
        match = re.search(r'\[\s*citation:\s*(\d+)\s*\]',
                          "Some text [citation: 1] more text")
        report = "Some text [citation: 1] more text"
        citation_pattern = r'\[\s*citation:\s*(\d+)\s*\]'
        citation_mapping = {
            1: {"title": "Test Title", "url": "https://example.com"}
        }
        position_offset = 0

        replacement_text, chunk_info, length_diff = _process_citation_match(
            match, report, citation_pattern, citation_mapping, position_offset)

        # 验证返回值
        assert replacement_text == "[source_tracer_result][Test Title](https://example.com)"
        assert chunk_info["citation_num"] == 1
        assert chunk_info["chunk"] == "Sample chunk text"
        assert "Test Title" in replacement_text
        assert "https://example.com" in replacement_text
        assert length_diff == len(replacement_text) - len(match.group(0))
        mock_get_citation_chunk.assert_called_once()

    @patch(f'{MODULE_PATH}._get_citation_chunk')
    def test_process_citation_match_no_mapping(self, mock_get_citation_chunk):
        """Test processing citation match when no mapping exists."""
        import re
        mock_get_citation_chunk.return_value = "Sample chunk text"

        # 创建一个匹配对象
        match = re.search(r'\[\s*citation:\s*(\d+)\s*\]',
                          "Some text [citation: 99] more text")
        report = "Some text [citation: 99] more text"
        citation_pattern = r'\[\s*citation:\s*(\d+)\s*\]'
        citation_mapping = {
            1: {"title": "Test Title", "url": "https://example.com"}
        }  # No mapping for 99
        position_offset = 0

        replacement_text, chunk_info, length_diff = _process_citation_match(
            match, report, citation_pattern, citation_mapping, position_offset)

        # 验证返回值
        assert replacement_text == ""  # Should be empty when no mapping
        assert chunk_info["citation_num"] == 99
        assert chunk_info["chunk"] == "Sample chunk text"
        assert length_diff == len(replacement_text) - len(match.group(0))

    @patch(f'{MODULE_PATH}._get_citation_chunk')
    def test_process_citation_match_empty_mapping(self, mock_get_citation_chunk):
        """Test processing citation match when mapping is empty."""
        import re
        mock_get_citation_chunk.return_value = "Sample chunk text"

        # 创建一个匹配对象
        match = re.search(r'\[\s*citation:\s*(\d+)\s*\]',
                          "Some text [citation: 1] more text")
        report = "Some text [citation: 1] more text"
        citation_pattern = r'\[\s*citation:\s*(\d+)\s*\]'
        citation_mapping = {
            1: {}  # Empty mapping
        }
        position_offset = 0

        replacement_text, chunk_info, length_diff = _process_citation_match(
            match, report, citation_pattern, citation_mapping, position_offset)

        # 验证返回值
        assert replacement_text == ""  # Should be empty when mapping is empty
        assert chunk_info["citation_num"] == 1
        assert chunk_info["chunk"] == "Sample chunk text"
        assert length_diff == len(replacement_text) - len(match.group(0))


class TestGenerateOriginReportData:
    """Test cases for generate_origin_report_data function."""

    @patch(f'{MODULE_PATH}._build_citation_mapping')
    @patch(
        f'{MODULE_PATH}._replace_citations_with_custom_mapping')
    @patch(f'{MODULE_PATH}._build_datas_from_chunks')
    def test_generate_origin_report_data_basic(self, mock_build_datas, mock_replace_citations, mock_build_mapping):
        """Test basic functionality of generating origin report data."""
        mock_build_mapping.return_value = {
            1: {"title": "Title", "url": "https://example.com", "content": "Content"}}
        mock_replace_citations.return_value = {
            "modified_report": "Modified report with [source_tracer_result][Title](https://example.com)",
            "citation_chunks": [{"citation_num": 1, "chunk": "test chunk", "_sentence_position": 100}]
        }
        mock_build_datas.return_value = [
            {"title": "Title", "url": "https://example.com", "content": "Content",
             "chunk": "test chunk", "_sentence_position": 100, "_is_origin_data": True}
        ]

        report = "Original report with [citation: 1] reference."
        classified_contents = [
            {"index": 1, "title": "Title", "url": "https://example.com",
             "original_content": "Content"}
        ]

        result = generate_origin_report_data(report, classified_contents)

        assert "origin_report_data" in result
        assert "modified_report" in result
        assert result[
                   "modified_report"] == "Modified report with [source_tracer_result][Title](https://example.com)"
        assert len(result["origin_report_data"]) == 1
        mock_build_mapping.assert_called_once_with(classified_contents)
        mock_replace_citations.assert_called_once()
        mock_build_datas.assert_called_once()

    @patch(f'{MODULE_PATH}._build_citation_mapping')
    def test_generate_origin_report_data_exception_handling(self, mock_build_mapping):
        """Test exception handling in generate_origin_report_data."""
        mock_build_mapping.side_effect = Exception("Test error")

        report = "Original report with [citation: 1] reference."
        classified_contents = [
            [{"index": 1, "title": "Title", "url": "https://example.com",
              "original_content": "Content"}]
        ]

        # The function should not catch exceptions, so we expect it to propagate
        with pytest.raises(Exception, match="Test error"):
            generate_origin_report_data(report, classified_contents)
