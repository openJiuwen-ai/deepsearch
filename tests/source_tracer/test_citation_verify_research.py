import asyncio
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from jiuwen_deepsearch.algorithm.source_trace.citation_verify_research import CitationVerifyResearch
from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode


class TestCitationVerifyResearch:
    """Test cases for CitationVerifyResearch core functionality."""

    def test_init(self):
        """Test CitationVerifyResearch initialization."""
        verifier = CitationVerifyResearch("mock_model")
        assert verifier.datas == []
        assert hasattr(verifier, 'concurrent_limit')
        assert isinstance(verifier.concurrent_limit, int)

    # Test static methods
    def test_find_matches_with_high_threshold(self):
        """Test find_matches with high threshold - should find exact matches."""
        text = "这是一个测试文本，包含一些特定内容。"
        fragments = ["测试文本", "特定内容"]
        threshold = 90

        result = CitationVerifyResearch.find_matches(
            text, fragments, threshold)

        assert len(result) == 2
        assert result[0][0] >= 0  # start position
        assert result[0][1] > result[0][0]  # end position > start position
        assert result[1][0] >= 0  # start position
        assert result[1][1] > result[1][0]  # end position > start position

    def test_find_matches_with_low_threshold(self):
        """Test find_matches with low threshold - should find more matches."""
        text = "这是一个测试文本，包含一些特定内容。"
        fragments = ["测试", "内容"]
        threshold = 60

        result = CitationVerifyResearch.find_matches(
            text, fragments, threshold)

        assert len(result) == 2
        # Each result should have start and end
        assert all(len(pos) == 2 for pos in result)

    def test_find_matches_no_matches(self):
        """Test find_matches with no matching fragments."""
        text = "这是一个测试文本"
        fragments = ["不存在的片段", "另一个不存在的片段"]
        threshold = 90

        result = CitationVerifyResearch.find_matches(
            text, fragments, threshold)

        assert len(result) == 0

    def test_find_matches_empty_fragments(self):
        """Test find_matches with empty fragments list."""
        text = "这是一个测试文本"
        fragments = []
        threshold = 90

        result = CitationVerifyResearch.find_matches(
            text, fragments, threshold)

        assert len(result) == 0

    def test_reorder_batch_results(self):
        """Test reorder_batch_results with normal data."""
        batches = [(0, ['item1']), (1, ['item2', 'item3'])]
        results = [['result1'], ['result2', 'result3']]
        batch_size = 1
        data_len = 3

        result = CitationVerifyResearch.reorder_batch_results(
            batches, results, batch_size, data_len)

        assert len(result) == 3
        assert result[0] == 'result1'
        assert result[1] == 'result2'
        assert result[2] == 'result3'

    def test_reorder_batch_results_with_none_results(self):
        """Test reorder_batch_results with None results."""
        batches = [(0, ['item1']), (1, ['item2'])]
        results = [['result1'], None]
        batch_size = 1
        data_len = 2

        result = CitationVerifyResearch.reorder_batch_results(
            batches, results, batch_size, data_len)

        assert len(result) == 2
        assert result[0] == 'result1'
        assert result[1] is None

    def test_reorder_batch_results_uneven_batches(self):
        """Test reorder_batch_results with uneven batch sizes."""
        batches = [(0, ['item1', 'item2']), (1, ['item3'])]
        results = [['result1', 'result2'], ['result3']]
        batch_size = 2
        data_len = 3

        result = CitationVerifyResearch.reorder_batch_results(
            batches, results, batch_size, data_len)

        assert len(result) == 3
        assert result[0] == 'result1'
        assert result[1] == 'result2'
        assert result[2] == 'result3'

    # Test instance methods
    def setup_method(self):
        """Set up test fixtures."""
        self.verifier = CitationVerifyResearch("mock_model")

    @patch('jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.LogManager')
    def test_prepare_batch_processing(self, mock_log_manager):
        """Test prepare_batch_processing method."""
        mock_log_manager.is_sensitive.return_value = False
        data = ['item1', 'item2', 'item3', 'item4', 'item5']
        batch_size = 2
        log_prefix = "test"

        batches, batch_state = self.verifier.prepare_batch_processing(
            data, batch_size, log_prefix)

        assert len(batches) == 3  # 5 items with batch size 2 = 3 batches
        assert batches[0] == (0, ['item1', 'item2'])
        assert batches[1] == (1, ['item3', 'item4'])
        assert batches[2] == (2, ['item5'])

        assert "results" in batch_state
        assert "running_tasks" in batch_state
        assert "completed_count" in batch_state
        assert "started_count" in batch_state
        assert len(batch_state["results"]) == 3
        assert batch_state["completed_count"] == 0
        assert batch_state["started_count"] == 0

    def test_prepare_handle_data(self):
        """Test prepare_handle_data method."""
        self.verifier.datas = [
            {'url': 'https://example.com',
             'content': 'test content', 'chunk': 'test chunk'},
            {'url': 'ftp://fileserver.com',
             'content': 'local content', 'chunk': 'local chunk'},
            {'url': '', 'content': 'empty url content', 'chunk': 'empty url chunk'}
        ]

        handle_datas, handle_index = self.verifier.prepare_handle_data()

        assert len(handle_datas) == 3
        assert len(handle_index) == 3
        assert handle_index == [0, 1, 2]

        # Check HTTP URL
        assert handle_datas[0]['domain'] == 'example.com'
        assert handle_datas[0]['citation_content'] == 'test content'
        assert handle_datas[0]['fact'] == 'test chunk'

        # Check non-HTTP URL
        assert handle_datas[1]['domain'] == ''
        assert handle_datas[1]['citation_content'] == 'local content'
        assert handle_datas[1]['fact'] == 'local chunk'

        # Check empty URL
        assert handle_datas[2]['domain'] == ''
        assert handle_datas[2]['citation_content'] == 'empty url content'
        assert handle_datas[2]['fact'] == 'empty url chunk'

        # Check original data is updated with valid flag
        assert self.verifier.datas[0]['valid'] is True
        assert self.verifier.datas[1]['valid'] is True
        assert self.verifier.datas[2]['valid'] is True

    def test_update_citation_data(self):
        """Test update_citation_data method."""
        self.verifier.datas = [
            {'content': 'original content 1', 'valid': True},
            {'content': 'original content 2', 'valid': True}
        ]
        handle_index = [0, 1]
        ordered_results = [
            {'source': 'test source 1', 'score': 0.9,
             'marked_citation_content': ['content 1']},
            {'source': 'test source 2', 'score': 0.7,
             'marked_citation_content': ['content 2']}
        ]

        self.verifier.update_citation_data(handle_index, ordered_results, self.verifier.datas)

        # Check first item
        assert self.verifier.datas[0]['source'] == 'test source 1'
        assert self.verifier.datas[0]['score'] == 0.9
        assert self.verifier.datas[0]['valid'] is True  # score >= 0.85

        # Check second item
        assert self.verifier.datas[1]['source'] == 'test source 2'
        assert self.verifier.datas[1]['score'] == 0.7
        assert self.verifier.datas[1]['valid'] is False  # score < 0.85

    def test_update_citation_data_length_mismatch(self):
        """Test update_citation_data with length mismatch."""
        self.verifier.datas = [{'content': 'content 1'}]
        handle_index = [0]
        ordered_results = [
            {'source': 'test source 1', 'score': 0.9},
            {'source': 'test source 2', 'score': 0.7}
        ]

        with pytest.raises(CustomValueException) as exc_info:
            self.verifier.update_citation_data(handle_index, ordered_results, self.verifier.datas)

        assert "LLM排序结果数量错误" in str(exc_info.value)
        assert StatusCode.CITATION_VERIFIER_DATA_LEN_ERROR.code == exc_info.value.error_code

    def test_update_citation_data_empty_marked_citation_content(self):
        """Test update_citation_data with empty marked_citation_content."""
        self.verifier.datas = [
            {'content': 'original content 1', 'valid': True},
            {'content': 'original content 2', 'valid': True}
        ]
        handle_index = [0, 1]
        ordered_results = [
            {'source': 'test source 1', 'score': 0.9,
             'marked_citation_content': []},
            {'source': 'test source 2', 'score': 0.9,
             'marked_citation_content': ['content 2']}
        ]

        self.verifier.update_citation_data(handle_index, ordered_results, self.verifier.datas)

        # Check first item - content should not be modified due to empty marked_citation_content
        assert self.verifier.datas[0]['content'] == 'original content 1'
        assert self.verifier.datas[0]['valid'] is False
        assert self.verifier.datas[0]['invalid_reason'] == 'marked citation content empty'

        # Check second item - content should be modified
        assert self.verifier.datas[1]['content'] != 'original content 2'
        assert self.verifier.datas[1]['valid'] is True
        assert '<mark>content 2</mark>' in self.verifier.datas[1]['content']

    def test_fuzzy_find_and_tag(self):
        """Test fuzzy_find_and_tag method."""
        text = "这是一个测试文本，包含一些特定内容。"
        fragments = ["测试文本", "特定内容"]

        result = self.verifier.fuzzy_find_and_tag(text, fragments)

        assert "<mark>测试文本</mark>" in result
        assert "<mark>特定内容</mark>" in result

    def test_fuzzy_find_and_tag_custom_template(self):
        """Test fuzzy_find_and_tag with custom tag template."""
        text = "这是一个测试文本，包含一些特定内容。"
        fragments = ["测试文本"]
        tag_template = "<highlight>{}</highlight>"

        result = self.verifier.fuzzy_find_and_tag(
            text, fragments, tag_template=tag_template)

        assert "<highlight>测试文本</highlight>" in result
        assert "<mark>" not in result  # Default template should not be used

    def test_fuzzy_find_and_tag_low_threshold(self):
        """Test fuzzy_find_and_tag with low threshold."""
        text = "这是一个测试文本，包含一些特定内容。"
        fragments = ["测试", "内容"]
        threshold = 60

        result = self.verifier.fuzzy_find_and_tag(
            text, fragments, threshold=threshold)

        # Should find matches with lower threshold
        assert "<mark>" in result
        assert "</mark>" in result

    def test_fuzzy_find_and_tag_no_matches(self):
        """Test fuzzy_find_and_tag with no matches."""
        text = "这是一个测试文本"
        fragments = ["不存在的片段"]
        threshold = 90

        result = self.verifier.fuzzy_find_and_tag(
            text, fragments, threshold=threshold)

        # Should return original text when no matches
        assert result == text

    def test_fuzzy_find_and_tag_empty_fragments(self):
        """Test fuzzy_find_and_tag with empty fragments."""
        text = "这是一个测试文本"
        fragments = []

        result = self.verifier.fuzzy_find_and_tag(text, fragments)

        # Should return original text when no fragments
        assert result == text

    @pytest.mark.asyncio
    async def test_process_batch_success(self):
        """Test process_batch with successful processing."""
        batch_state = {
            "results": [None, None],
            "running_tasks": set(),
            "completed_count": 0,
            "started_count": 0
        }
        batch_idx = 0
        batch = ["item1", "item2"]
        process_func = AsyncMock(return_value=["result1", "result2"])

        def error_result_func(b): return [f"error_{item}" for item in b]

        semaphore = asyncio.Semaphore(2)
        log_prefix = "test"

        await self.verifier.process_batch(
            batch_state, batch_idx, batch, process_func, error_result_func, semaphore, log_prefix
        )

        assert batch_state["results"][0] == ["result1", "result2"]
        assert batch_state["completed_count"] == 1
        assert batch_state["started_count"] == 1
        assert batch_idx not in batch_state["running_tasks"]

    @pytest.mark.asyncio
    async def test_process_batch_with_exception(self):
        """Test process_batch with exception during processing."""
        batch_state = {
            "results": [None],
            "running_tasks": set(),
            "completed_count": 0,
            "started_count": 0
        }
        batch_idx = 0
        batch = ["item1"]
        process_func = AsyncMock(side_effect=Exception("Test error"))

        def error_result_func(b): return [f"error_{item}" for item in b]

        semaphore = asyncio.Semaphore(2)
        log_prefix = "test"

        await self.verifier.process_batch(
            batch_state, batch_idx, batch, process_func, error_result_func, semaphore, log_prefix
        )

        assert batch_state["results"][0] == ["error_item1"]
        assert batch_state["completed_count"] == 1
        assert batch_state["started_count"] == 1
        assert batch_idx not in batch_state["running_tasks"]

    @pytest.mark.asyncio
    @patch('jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.LogManager')
    async def test_process_batch_with_logging(self, mock_log_manager):
        """Test process_batch with logging enabled."""
        mock_log_manager.is_sensitive.return_value = False
        batch_state = {
            "results": [None],
            "running_tasks": set(),
            "completed_count": 0,
            "started_count": 0
        }
        batch_idx = 0
        batch = ["item1"]
        process_func = AsyncMock(side_effect=Exception("Test error"))

        def error_result_func(b): return [f"error_{item}" for item in b]

        semaphore = asyncio.Semaphore(2)
        log_prefix = "test"

        await self.verifier.process_batch(
            batch_state, batch_idx, batch, process_func, error_result_func, semaphore, log_prefix
        )

        # Verify error was logged with details
        assert batch_state["results"][0] == ["error_item1"]
        assert batch_state["completed_count"] == 1
        assert batch_state["started_count"] == 1

    @pytest.mark.asyncio
    async def test_execute_batch_tasks(self):
        """Test execute_batch_tasks method."""
        batches = [(0, ["item1"]), (1, ["item2"])]
        batch_state = {
            "results": [None, None],
            "running_tasks": set(),
            "completed_count": 0,
            "started_count": 0
        }
        process_func = AsyncMock(side_effect=lambda batch: [
            f"result_{item}" for item in batch])

        def error_func(b): return [f"error_{item}" for item in b]

        log_prefix = "test"

        await self.verifier.execute_batch_tasks(batches, batch_state, process_func, error_func, log_prefix)

        assert batch_state["results"][0] == ["result_item1"]
        assert batch_state["results"][1] == ["result_item2"]
        assert batch_state["completed_count"] == 2
        assert batch_state["started_count"] == 2

    @pytest.mark.asyncio
    async def test_process_batches_with_concurrency(self):
        """Test process_batches_with_concurrency method."""
        data = ["item1", "item2", "item3"]
        batch_size = 1
        process_func = AsyncMock(side_effect=lambda batch: [
            f"result_{item}" for item in batch])

        def error_func(b): return [f"error_{item}" for item in b]

        log_prefix = "test"

        result = await self.verifier.process_batches_with_concurrency(
            data, batch_size, process_func, error_func, log_prefix
        )

        assert len(result) == 3
        assert result[0] == "result_item1"
        assert result[1] == "result_item2"
        assert result[2] == "result_item3"

    @pytest.mark.asyncio
    async def test_extract_messages_batch_success(self):
        """Test extract_messages_batch with successful LLM response."""
        handle_datas = [
            {"domain": "example.com", "citation_content": "这是内容中的标记1。", "fact": "事实 1"},
            {"domain": "test.com", "citation_content": "这里在内容中包含标记2。", "fact": "事实 2"}
        ]
        expected_result = [
            {"source": "来源 1", "score": 0.9,
             "marked_citation_content": ["标记1"]},
            {"source": "来源 2", "score": 0.8,
             "marked_citation_content": ["标记2"]}
        ]
        expected_result_after_fuzzy_match = [
            {"source": "来源 1", "score": 0.9,
             "marked_citation_content": ["标记1"]},  # "标记1" 在 "这是内容中的标记1。" 中匹配
            {"source": "来源 2", "score": 0.8,
             "marked_citation_content": ["标记2"]}]  # "标记2" 模糊匹配为 "标记2"

        with patch.object(self.verifier, 'call_model', new_callable=AsyncMock) as mock_call_model:
            mock_call_model.return_value = json.dumps(expected_result)
            with patch(
                    'jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.apply_system_prompt') as mock_prompt:
                mock_prompt.return_value = [
                    {"role": "system", "content": "test prompt"}]

                result = await self.verifier.extract_messages_batch(handle_datas)

                assert result == expected_result_after_fuzzy_match
                mock_call_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_messages_batch_with_retries(self):
        """Test extract_messages_batch with retries on failure."""
        handle_datas = [{"domain": "example.com",
                         "citation_content": "This contains the marked 1 text.", "fact": "fact 1"}]
        expected_result = [
            {"source": "source 1", "score": 0.9, "marked_citation_content": ["marked 1"]}]

        with patch.object(self.verifier, 'call_model', new_callable=AsyncMock) as mock_call_model:
            mock_call_model.side_effect = [
                Exception("First error"),
                Exception("Second error"),
                json.dumps(expected_result)
            ]
            with patch(
                    'jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.apply_system_prompt') as mock_prompt:
                mock_prompt.return_value = [
                    {"role": "system", "content": "test prompt"}]

                result = await self.verifier.extract_messages_batch(handle_datas)

                assert result == expected_result
                assert mock_call_model.call_count == 3

    @pytest.mark.asyncio
    async def test_extract_messages_batch_length_mismatch(self):
        """Test extract_messages_batch with length mismatch."""
        handle_datas = [
            {"domain": "example.com", "citation_content": "content 1", "fact": "fact 1"},
            {"domain": "test.com", "citation_content": "content 2", "fact": "fact 2"}
        ]

        # Test the update_citation_data method directly instead
        self.verifier.datas = [
            {'content': 'original content 1', 'valid': True},
            {'content': 'original content 2', 'valid': True}
        ]
        handle_index = [0, 1]
        ordered_results = [
            # Only one result for two inputs
            {'source': 'test source 1', 'score': 0.9}
        ]

        with pytest.raises(CustomValueException) as exc_info:
            self.verifier.update_citation_data(handle_index, ordered_results, self.verifier.datas)

        assert "LLM排序结果数量错误" in str(exc_info.value)
        assert StatusCode.CITATION_VERIFIER_DATA_LEN_ERROR.code == exc_info.value.error_code

    @pytest.mark.asyncio
    async def test_extract_messages_batch_max_retries(self):
        """Test extract_messages_batch with max retries exceeded."""
        handle_datas = [{"domain": "example.com",
                         "citation_content": "content 1", "fact": "fact 1"}]

        with patch.object(self.verifier, 'call_model', new_callable=AsyncMock) as mock_call_model:
            mock_call_model.side_effect = Exception("Always error")
            with patch(
                    'jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.apply_system_prompt') as mock_prompt:
                mock_prompt.return_value = [
                    {"role": "system", "content": "test prompt"}]

                result = await self.verifier.extract_messages_batch(handle_datas)

                # Should return empty dict when max retries exceeded
                assert result == [{'extract_failed_reason': 'LLM retry times exceeded'}]
                assert mock_call_model.call_count == 3

    @pytest.mark.asyncio
    async def test_get_source_date_mark_score(self):
        """Test get_source_date_mark_score method."""
        self.verifier.datas = [
            {'url': 'https://example.com',
             'content': 'test content 1', 'chunk': 'test chunk 1'},
            {'url': 'https://test.com', 'content': 'test content 2',
             'chunk': 'test chunk 2'}
        ]

        with patch.object(self.verifier, 'process_batches_with_concurrency', new_callable=AsyncMock) as mock_process:
            mock_process.return_value = [
                {'source': 'source 1', 'score': 0.9,
                 'marked_citation_content': ['marked 1']},
                {'source': 'source 2', 'score': 0.8,
                 'marked_citation_content': ['marked 2']}
            ]

            await self.verifier.get_source_date_mark_score()

            # Check that datas were updated
            assert self.verifier.datas[0]['source'] == 'source 1'
            assert self.verifier.datas[0]['score'] == 0.9
            assert self.verifier.datas[1]['source'] == 'source 2'
            assert self.verifier.datas[1]['score'] == 0.8
            assert self.verifier.datas[1]['valid'] is False  # score < 0.85

    @pytest.mark.asyncio
    async def test_get_source_date_mark_score_empty_data(self):
        """Test get_source_date_mark_score with empty data."""
        self.verifier.datas = []

        await self.verifier.get_source_date_mark_score()

        # Should not raise any error
        assert self.verifier.datas == []

    @pytest.mark.asyncio
    async def test_run(self):
        """Test run method."""
        datas = [
            {'url': 'https://example.com',
             'content': 'test content 1', 'chunk': 'test chunk 1'},
            {'url': 'https://test.com', 'content': 'test content 2',
             'chunk': 'test chunk 2'}
        ]

        with patch.object(self.verifier, 'get_source_date_mark_score', new_callable=AsyncMock) as mock_get:
            await self.verifier.run(datas)

            assert self.verifier.datas == datas
            mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_model(self):
        """Test call_model method."""
        user_prompt = ["test prompt"]
        # 返回合法的 list[dict] 结构的 JSON 字符串
        expected_raw_content = '[{"source": "test_source", "score": 0.9, "marked_citation_content": ["test"]}]'
        expected_response = {"content": expected_raw_content}

        with patch('jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.llm_context') as mock_llm_wrapper:
            mock_llm_model = MagicMock()
            mock_llm_wrapper.get_llm_model.return_value = mock_llm_model

            with patch('jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.ainvoke_llm_with_stats',
                       new_callable=AsyncMock) as mock_ainvoke:
                mock_ainvoke.return_value = expected_response

                with patch(
                        'jiuwen_deepsearch.algorithm.source_trace.citation_verify_research.normalize_json_output') as mock_normalize:
                    mock_normalize.return_value = expected_raw_content
                    result = await self.verifier.call_model(user_prompt)
                    assert result == expected_raw_content
                    mock_normalize.assert_called_once_with(expected_raw_content)
