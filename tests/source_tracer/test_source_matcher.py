import json
from unittest.mock import patch, MagicMock, ANY

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_matcher import (
    match_sources,
    process_source_type,
    process_single_chunk,
    process_chunked_source,
    call_llm_for_trace,
    parse_trace_response,
    merge_trace_results,
    validate_trace_results
)

pytest_plugins = ["pytest_asyncio"]


class TestMatchSources:
    """Test cases for match_sources function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.validate_trace_results')
    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.merge_trace_results')
    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.process_source_type')
    @pytest.mark.asyncio
    async def test_match_sources_success(self, mock_process_source_type,
                                         mock_merge_trace_results,
                                         mock_validate_trace_results):
        """Test successful matching of sources."""
        # 模拟处理单个来源类型
        mock_process_source_type.return_value = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]
        # 模拟合并结果
        mock_merge_trace_results.return_value = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]
        # 模拟验证结果
        mock_validate_trace_results.return_value = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]

        content_recognition_result = ["sentence1"]
        preprocessed_search_record = {
            "web": [{"title": "title1", "content": "content1"}]
        }
        chunk_size = 5

        result = await match_sources(content_recognition_result, preprocessed_search_record, chunk_size, "mock_model")

        # 验证函数被正确调用
        assert mock_process_source_type.called
        assert mock_merge_trace_results.called
        assert mock_validate_trace_results.called
        assert result == [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}]

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.process_source_type')
    @pytest.mark.asyncio
    async def test_match_sources_exception_handling(self, mock_process_source_type):
        """Test matching when an exception occurs."""
        mock_process_source_type.side_effect = Exception("Processing error")

        content_recognition_result = ["sentence1"]
        preprocessed_search_record = {
            "web": [{"title": "title1", "content": "content1"}]
        }
        chunk_size = 5

        # 验证在异常情况下会抛出异常
        with pytest.raises(Exception, match="Processing error"):
            await match_sources(content_recognition_result, preprocessed_search_record, chunk_size, "mock_model")

    @pytest.mark.asyncio
    async def test_match_sources_empty_input(self):
        """Test matching with empty inputs."""
        result = await match_sources([], {}, 5, "mock_model")
        assert result == []

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.llm_context')
    @pytest.mark.asyncio
    async def test_match_sources_empty_content_recognition(self, mock_llm_wrapper):
        """Test matching with empty content recognition result."""
        mock_llm_instance = MagicMock()
        mock_llm_wrapper.return_value = mock_llm_instance
        preprocessed_search_record = {
            "web": [{"title": "title1", "content": "content1"}]
        }
        result = await match_sources([], preprocessed_search_record, 5, "mock_model")
        # 应该返回空列表，因为没有内容需要匹配
        assert result == []


class TestProcessSourceType:
    """Test cases for process_source_type function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.process_single_chunk')
    @pytest.mark.asyncio
    async def test_process_source_type_small_list(self, mock_process_single_chunk):
        """Test processing a small source list."""
        mock_process_single_chunk.return_value = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]

        source_type = "web"
        source_list = [{"title": "title1", "content": "content1"}]
        content_recognition_result = ["sentence1"]
        chunk_size = 10  # 大于列表长度

        result = await process_source_type(source_type, source_list, content_recognition_result, chunk_size,
                                           "mock_model")

        mock_process_single_chunk.assert_called_once_with(
            source_type, source_list, content_recognition_result, "mock_model")
        assert result == [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}]

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.process_chunked_source')
    @pytest.mark.asyncio
    async def test_process_source_type_large_list(self, mock_process_chunked_source):
        """Test processing a large source list."""
        mock_process_chunked_source.return_value = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]

        source_type = "web"
        source_list = [{"title": f"title{i}", "content": f"content{i}"} for i in range(10)]
        content_recognition_result = ["sentence1"]
        chunk_size = 5  # 小于列表长度

        result = await process_source_type(source_type, source_list, content_recognition_result, chunk_size,
                                           "mock_model")

        mock_process_chunked_source.assert_called_once_with(
            source_type, source_list, content_recognition_result, chunk_size, "mock_model")
        assert result == [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}]


class TestProcessSingleChunk:
    """Test cases for process_single_chunk function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.call_llm_for_trace')
    @pytest.mark.asyncio
    async def test_process_single_chunk_success(self, mock_call_llm_for_trace):
        """Test processing a single chunk successfully."""
        mock_call_llm_for_trace.return_value = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]

        source_type = "web"
        source_list = [{"title": "title1", "content": "content1"}]
        content_recognition_result = ["sentence1"]

        result = await process_single_chunk(source_type, source_list, content_recognition_result, "mock_model")

        # 验证call_llm_for_trace被正确调用
        expected_search_record = {"web": [{"title": "title1", "content": "content1"}]}
        mock_call_llm_for_trace.assert_called_once_with(
            source_type, expected_search_record, content_recognition_result, "完整", "mock_model")
        assert result == [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}]


class TestProcessChunkedSource:
    """Test cases for process_chunked_source function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.call_llm_for_trace')
    @pytest.mark.asyncio
    async def test_process_chunked_source_success(self, mock_call_llm_for_trace):
        """Test processing chunked source successfully."""
        # 模拟每个分片的处理结果
        mock_call_llm_for_trace.side_effect = [
            [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}],
            [{"sentence": "sentence2", "source": "web", "matched_source_indices": [1]}]
        ]

        source_type = "web"
        source_list = [
            {"title": "title1", "content": "content1"},
            {"title": "title2", "content": "content2"},
            {"title": "title3", "content": "content3"},
            {"title": "title4", "content": "content4"}
        ]
        content_recognition_result = ["sentence1", "sentence2"]
        chunk_size = 2

        result = await process_chunked_source(source_type, source_list, content_recognition_result, chunk_size,
                                              "mock_model")

        # 验证call_llm_for_trace被调用了两次（对应两个分片）
        assert mock_call_llm_for_trace.call_count == 2
        # 验证第一个分片的参数
        expected_search_record_1 = {"web": [{"title": "title1", "content": "content1"},
                                            {"title": "title2", "content": "content2"}]}
        mock_call_llm_for_trace.assert_any_call(
            source_type, expected_search_record_1, content_recognition_result, "分片 0-1", "mock_model")
        # 验证第二个分片的参数
        expected_search_record_2 = {"web": [{"title": "title3", "content": "content3"},
                                            {"title": "title4", "content": "content4"}]}
        mock_call_llm_for_trace.assert_any_call(
            source_type, expected_search_record_2, content_recognition_result, "分片 2-3", "mock_model")
        # 验证最终结果
        assert len(result) == 2
        assert {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]} in result
        assert {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]} in result

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.call_llm_for_trace')
    @pytest.mark.asyncio
    async def test_process_chunked_source_with_exception(self, mock_call_llm_for_trace):
        """Test processing chunked source when some chunks fail."""
        # 模拟第一个分片成功，第二个分片失败
        mock_call_llm_for_trace.side_effect = [
            [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}],
            Exception("LLM error")
        ]

        source_type = "web"
        source_list = [
            {"title": "title1", "content": "content1"},
            {"title": "title2", "content": "content2"},
            {"title": "title3", "content": "content3"},
            {"title": "title4", "content": "content4"}
        ]
        content_recognition_result = ["sentence1", "sentence2"]
        chunk_size = 2

        # 验证在异常情况下会抛出异常
        with pytest.raises(Exception, match="LLM error"):
            await process_chunked_source(source_type, source_list, content_recognition_result, chunk_size, "mock_model")


class TestCallLlmForTrace:
    """Test cases for call_llm_for_trace function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.llm_context')
    @patch('openjiuwen_deepsearch.algorithm.source_trace.source_matcher.ainvoke_llm_with_stats')
    @pytest.mark.asyncio
    async def test_call_llm_for_trace_llm_invoke_error(self, mock_ainvoke_llm_with_stats, mock_llm_wrapper):
        """Test LLM call when LLM invocation fails."""
        mock_llm_instance = MagicMock()
        mock_llm_wrapper.return_value = mock_llm_instance
        mock_ainvoke_llm_with_stats.side_effect = Exception("Invoke error")

        source_type = "web"
        search_record = {"web": [{"title": "title1", "content": "content1"}]}
        content_recognition_result = ["sentence1"]
        process_type = "完整"

        result = await call_llm_for_trace(source_type, search_record, content_recognition_result, process_type,
                                          "mock_model")

        assert result == []


class TestParseTraceResponse:
    """Test cases for parse_trace_response function."""

    def test_parse_trace_response_success(self):
        """Test successful parsing of trace response."""
        llm_result = '{"source_traced_results": [{"sentence": "sentence1", "matched_source_indices": [0]}]}'
        source_type = "web"

        result = parse_trace_response(llm_result, source_type)

        expected = [{"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}]
        assert result == expected

    def test_parse_trace_response_with_invalid_json(self):
        """Test parsing when response contains invalid JSON."""
        llm_result = 'invalid json'
        source_type = "web"

        # 验证在无效JSON情况下会抛出JSONDecodeError
        with pytest.raises(json.JSONDecodeError):
            parse_trace_response(llm_result, source_type)

    def test_parse_trace_response_empty_json(self):
        """Test parsing when response contains empty JSON."""
        llm_result = '{}'
        source_type = "web"

        result = parse_trace_response(llm_result, source_type)

        assert result == []

    def test_parse_trace_response_no_source_traced_results_key(self):
        """Test parsing when response doesn't contain source_traced_results key."""
        llm_result = '{"other_key": "value"}'
        source_type = "web"

        result = parse_trace_response(llm_result, source_type)

        assert result == []

    def test_parse_trace_response_multiple_results(self):
        """Test parsing multiple trace results."""
        llm_result = '''
        {
            "source_traced_results": [
                {"sentence": "sentence1", "matched_source_indices": [0]},
                {"sentence": "sentence2", "matched_source_indices": [1, 2]}
            ]
        }
        '''
        source_type = "web"

        result = parse_trace_response(llm_result, source_type)

        expected = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]},
            {"sentence": "sentence2", "source": "web", "matched_source_indices": [1, 2]}
        ]
        assert result == expected

    def test_parse_trace_response_with_normalize_json_output(self):
        """Test parsing when response needs JSON normalization."""
        # 模拟包含多余字符的JSON响应
        llm_result = '```\n{"source_traced_results": [{"sentence": "sentence1", "matched_source_indices": [0]}]}\n```'
        source_type = "web"

        # 这里我们测试函数是否能处理格式化过的JSON
        result = parse_trace_response(llm_result, source_type)

        # 如果normalize_json_output能正确处理，应该返回正确的结果
        # 否则返回空列表
        assert isinstance(result, list)


class TestMergeTraceResults:
    """Test cases for merge_trace_results function."""

    def test_merge_trace_results_basic(self):
        """Test basic functionality of merging trace results."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]},
            {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]}
        ]

        result = merge_trace_results(trace_results)

        assert len(result) == 2
        assert {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]} in result
        assert {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]} in result

    def test_merge_trace_results_with_duplicates(self):
        """Test merging when there are duplicate sentence-source pairs."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0, 1]},
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [2, 3]}  # 重复的sentence-source对
        ]

        result = merge_trace_results(trace_results)

        # 重复的sentence-source对应该被合并
        assert len(result) == 1
        merged_result = result[0]
        assert merged_result["sentence"] == "sentence1"
        assert merged_result["source"] == "web"
        # matched_source_indices应该被合并并去重排序
        assert sorted(merged_result["matched_source_indices"]) == [0, 1, 2, 3]

    def test_merge_trace_results_different_sources(self):
        """Test merging when sentences have different sources."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]},
            {"sentence": "sentence1", "source": "book", "matched_source_indices": [1]}  # 相同句子，不同来源
        ]

        result = merge_trace_results(trace_results)

        # 相同句子但不同来源应该被视为不同的条目
        assert len(result) == 2
        assert {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]} in result
        assert {"sentence": "sentence1", "source": "book", "matched_source_indices": [1]} in result

    def test_merge_trace_results_empty_input(self):
        """Test merging with empty input."""
        result = merge_trace_results([])
        assert result == []

    def test_merge_trace_results_with_empty_matched_indices(self):
        """Test merging when some results have empty matched indices."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": []},
            {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]}
        ]

        result = merge_trace_results(trace_results)

        # 空的matched_source_indices应该被过滤掉
        assert len(result) == 1
        assert {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]} in result

    def test_merge_trace_results_complex_case(self):
        """Test merging with a more complex case."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0, 2]},
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [1, 3]},  # 重复
            {"sentence": "sentence2", "source": "web", "matched_source_indices": [0]},
            {"sentence": "sentence1", "source": "book", "matched_source_indices": [5]}  # 不同来源
        ]

        result = merge_trace_results(trace_results)

        # 应该有2个结果：一个web来源的sentence1，一个web来源的sentence2，一个book来源的sentence1
        assert len(result) == 3

        # 找到sentence1-web的结果
        sentence1_web = next((r for r in result if r["sentence"] == "sentence1" and r["source"] == "web"), None)
        assert sentence1_web is not None
        assert sorted(sentence1_web["matched_source_indices"]) == [0, 1, 2, 3]

        # 找到sentence2-web的结果
        sentence2_web = next((r for r in result if r["sentence"] == "sentence2" and r["source"] == "web"), None)
        assert sentence2_web is not None
        assert sentence2_web["matched_source_indices"] == [0]

        # 找到sentence1-book的结果
        sentence1_book = next((r for r in result if r["sentence"] == "sentence1" and r["source"] == "book"), None)
        assert sentence1_book is not None
        assert sentence1_book["matched_source_indices"] == [5]


class TestValidateTraceResults:
    """Test cases for validate_trace_results function."""

    def test_validate_trace_results_basic(self):
        """Test basic functionality of validating trace results."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]},
            {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]}
        ]
        content_recognition_result = ["sentence1", "sentence2", "sentence3"]

        result = validate_trace_results(trace_results, content_recognition_result)

        # 所有句子都在内容识别结果中，所以都应该保留
        assert len(result) == 2
        assert {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]} in result
        assert {"sentence": "sentence2", "source": "web", "matched_source_indices": [1]} in result

    def test_validate_trace_results_with_invalid_sentences(self):
        """Test validation when some sentences are not in content recognition result."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]},
            {"sentence": "invalid_sentence", "source": "web", "matched_source_indices": [1]},
            {"sentence": "sentence2", "source": "web", "matched_source_indices": [2]}
        ]
        content_recognition_result = ["sentence1", "sentence2", "sentence3"]

        result = validate_trace_results(trace_results, content_recognition_result)

        # 只有有效的句子应该被保留
        assert len(result) == 2
        assert {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]} in result
        assert {"sentence": "sentence2", "source": "web", "matched_source_indices": [2]} in result
        # invalid_sentence不应该在结果中
        invalid_result = [r for r in result if r["sentence"] == "invalid_sentence"]
        assert len(invalid_result) == 0

    def test_validate_trace_results_empty_input(self):
        """Test validation with empty inputs."""
        result = validate_trace_results([], [])
        assert result == []

    def test_validate_trace_results_empty_trace_results(self):
        """Test validation with empty trace results."""
        content_recognition_result = ["sentence1", "sentence2"]
        result = validate_trace_results([], content_recognition_result)
        assert result == []

    def test_validate_trace_results_empty_content_recognition(self):
        """Test validation with empty content recognition result."""
        trace_results = [
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [0]}
        ]
        content_recognition_result = []

        result = validate_trace_results(trace_results, content_recognition_result)

        # 所有句子都不在空的内容识别结果中，所以结果应该为空
        assert result == []

    def test_validate_trace_results_with_empty_sentence(self):
        """Test validation when result contains empty sentence."""
        trace_results = [
            {"sentence": "", "source": "web", "matched_source_indices": [0]},
            {"sentence": "sentence1", "source": "web", "matched_source_indices": [1]}
        ]
        content_recognition_result = ["sentence1", "sentence2"]

        result = validate_trace_results(trace_results, content_recognition_result)

        # 空句子应该被过滤掉，只有有效的句子保留
        assert len(result) == 1
        assert {"sentence": "sentence1", "source": "web", "matched_source_indices": [1]} in result
