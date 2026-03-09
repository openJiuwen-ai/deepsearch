from dataclasses import dataclass
from typing import Any, List, Dict, Tuple
from unittest.mock import patch

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_tracer import SourceTracer
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

MODULE_PATH = "openjiuwen_deepsearch.algorithm.source_trace.source_tracer"


@dataclass
class SourceTracerTestData:
    """Dataclass to hold mock return values for SourceTracer tests."""
    origin_report: str
    preprocess_report_return: Tuple[str, str]
    recognize_content_return: List[str]
    match_sources_return: List[Dict[str, Any]]
    generate_source_datas_return: List[Dict[str, Any]]


class TestSourceTracer:
    """Test cases for SourceTracer class."""

    @pytest.fixture
    def source_tracer_test_data(self, origin_report_value, mock_preprocess_report_return_value,
                                mock_recognize_content_to_cite_return_value,
                                mock_match_sources_return_value,
                                mock_generate_source_datas_return_value):
        """Fixture to provide grouped mock return values."""
        return SourceTracerTestData(
            origin_report=origin_report_value,
            preprocess_report_return=mock_preprocess_report_return_value,
            recognize_content_return=mock_recognize_content_to_cite_return_value,
            match_sources_return=mock_match_sources_return_value,
            generate_source_datas_return=mock_generate_source_datas_return_value
        )

    @pytest.fixture
    def mock_algorithm_inputs(self, origin_report_value, origin_search_record):
        """Fixture to provide mock algorithm inputs."""
        return {
            "report": origin_report_value,
            "merged_trace_source_datas": [],
            "classified_content": origin_search_record.get("web_page_search_record", [])
        }

    @pytest.fixture
    def source_tracer_instance(self, mock_algorithm_inputs):
        """Fixture to create a SourceTracer instance."""
        return SourceTracer(mock_algorithm_inputs)

    @pytest.fixture
    def origin_report_value(self):
        return "This is a test report."

    @pytest.fixture
    def origin_search_record(self):
        search_record = {
            "web_page_search_record": [
                {"title": "example", "url": "https://example.com", "original_content": "test content"}],
            "web_image_search_record": [],
            "local_text_search_record": [],
            "local_image_search_record": []
        }
        return search_record

    @pytest.fixture
    def mock_preprocess_report_return_value(self):
        return "removed section", "This is a preprocessed report."

    @pytest.fixture
    def mock_recognize_content_to_cite_return_value(self):
        return ["test"]

    @pytest.fixture
    def mock_match_sources_return_value(self):
        return [{"sentence": "test", "matched_source_indices": [1, 2, 3]}]

    @pytest.fixture
    def mock_generate_source_datas_return_value(self):
        data = {
            "name": "",  # 引用名（报告名）
            "url": "",  # 引用链接
            "title": "example",  # 引用网页标题
            "content": "test content",  # 引用内容摘要
            "source": "",  # 引用来源
            "publish_time": "",  # 信息发布时间
            "from": "",  # 表明是本地信息或网页信息
            "chunk": "test",  # 报告中需要添加引用的句子
            "score": 0.0,  # 引用内容置信度
            "id": "",  # 行内引用唯一标识
        }
        return [data]

    @pytest.fixture
    def mock_classified_content_value(self):
        return [{"index": 1, "title": "example", "url": "https://example.com", "original_content": "test content"}]

    # research_trace_source tests

    @pytest.mark.asyncio
    async def test_research_trace_source_empty_report(self, mock_algorithm_inputs):
        """Test research_trace_source when report is empty."""
        # Arrange
        mock_algorithm_inputs["report"] = ""
        tracer = SourceTracer(mock_algorithm_inputs)

        # Act
        await tracer.research_trace_source()

        # Assert
        assert getattr(tracer, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_preprocess_search_record_empty(self, mock_algorithm_inputs,
                                                                        origin_report_value):
        """Test research_trace_source when search record preprocessing returns empty."""
        # Arrange
        mock_algorithm_inputs["search_record"] = {
            "web_page_search_record": [],
            "web_image_search_record": [],
            "local_text_search_record": [],
            "local_image_search_record": []
        }
        tracer = SourceTracer(mock_algorithm_inputs)

        # Act
        await tracer.research_trace_source()

        # Assert
        assert getattr(tracer, '_trace_source_datas') == []

    @pytest.mark.asyncio
    @staticmethod
    async def test_research_trace_source_preprocess_report_called(source_tracer_instance, source_tracer_test_data):
        """Test that preprocess_report is called in research_trace_source."""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = source_tracer_test_data.preprocess_report_return

            # Need to patch other async dependencies
            with patch(
                    f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize:
                mock_recognize.return_value = source_tracer_test_data.recognize_content_return

                with patch(f'{MODULE_PATH}.match_sources') as mock_match:
                    mock_match.return_value = source_tracer_test_data.match_sources_return

                    with patch(
                            f'{MODULE_PATH}.generate_source_datas') as mock_generate:
                        mock_generate.return_value = source_tracer_test_data.generate_source_datas_return

                        # Act
                        await source_tracer_instance.research_trace_source()

                        # Assert
                        mock_preprocess.assert_called_once_with(
                            source_tracer_test_data.origin_report)
                        assert (getattr(source_tracer_instance, '_trace_source_datas') ==
                                source_tracer_test_data.generate_source_datas_return)

    @pytest.mark.asyncio
    async def test_research_trace_source_recognition_failure(self, source_tracer_instance, origin_report_value,
                                                             mock_preprocess_report_return_value):
        """Test research_trace_source when content recognition fails."""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize:
                mock_recognize.return_value = []

                # Act
                await source_tracer_instance.research_trace_source()

                # Assert
                assert getattr(source_tracer_instance, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_matching_failure(self, source_tracer_instance, origin_report_value,
                                                          mock_preprocess_report_return_value,
                                                          mock_recognize_content_to_cite_return_value):
        """Test research_trace_source when source matching fails."""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize:
                mock_recognize.return_value = mock_recognize_content_to_cite_return_value

                with patch(f'{MODULE_PATH}.match_sources') as mock_match:
                    mock_match.return_value = []

                    # Act
                    result = await source_tracer_instance.research_trace_source()

                    # Assert
                    assert getattr(source_tracer_instance, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_with_trace_source_datas(self, mock_algorithm_inputs, source_tracer_test_data):
        """Test research_trace_source when merged_trace_source_datas is provided."""
        mock_algorithm_inputs["merged_trace_source_datas"] = [
            {"existing": "data"}]
        tracer = SourceTracer(mock_algorithm_inputs)

        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = source_tracer_test_data.preprocess_report_return

            with patch(
                    f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize:
                mock_recognize.return_value = source_tracer_test_data.recognize_content_return

                with patch(f'{MODULE_PATH}.match_sources') as mock_match:
                    mock_match.return_value = source_tracer_test_data.match_sources_return

                    with patch(
                            f'{MODULE_PATH}.generate_source_datas') as mock_generate:
                        mock_generate.return_value = source_tracer_test_data.generate_source_datas_return

                        # Act
                        await tracer.research_trace_source()

                        # Assert
                        assert (getattr(tracer, '_trace_source_datas') ==
                        source_tracer_test_data.generate_source_datas_return)

    @pytest.mark.asyncio
    async def test_research_trace_source_exception_handling(self, source_tracer_instance, origin_report_value):
        """Test research_trace_source exception handling."""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.side_effect = Exception("Test error")

            # Act & Assert
            with pytest.raises(CustomValueException) as exc_info:
                await source_tracer_instance.research_trace_source()

            # Verify it's the expected exception type
            assert exc_info.value.error_code == StatusCode.SOURCE_TRACER_TRACE_SOURCE_ERROR.code

    # add_source_to_report tests
    def test_add_source_to_report_empty_search_record(self, mock_algorithm_inputs, origin_report_value):
        """Test add_source_to_report when search record preprocessing fails."""
        # Arrange
        mock_algorithm_inputs["search_record"] = {
            "web_page_search_record": [],
            "web_image_search_record": [],
            "local_text_search_record": [],
            "local_image_search_record": []
        }
        tracer = SourceTracer(mock_algorithm_inputs)

        # Act
        result = tracer.add_source_to_report()

        # Assert
        assert result == {
            "modified_report": origin_report_value,
            "datas": []
        }

    @staticmethod
    def test_add_source_to_report_with_classified_content(mock_algorithm_inputs, mock_classified_content_value,
                                                          mock_preprocess_report_return_value):
        """Test add_source_to_report with classified_content parameter."""
        # Arrange
        mock_algorithm_inputs["classified_content"] = mock_classified_content_value
        tracer = SourceTracer(mock_algorithm_inputs)

        with patch(
                f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin:
                mock_generate_origin.return_value = {
                    "origin_report_data": [{"type": "reference", "content": "Existing reference [1]"}],
                    "modified_report": "modified report"
                }

                with patch(
                        f'{MODULE_PATH}.merge_source_datas') as mock_merge:
                    mock_merge.return_value = [{"merged": "data"}]

                    with patch(
                            f'{MODULE_PATH}.add_source_references') as mock_add_source:
                        mock_add_source.return_value = (
                            "final report", [{"final": "data"}])

                        # Act
                        result = tracer.add_source_to_report()

                        # Assert
                        mock_preprocess_report.assert_called_once()
                        mock_generate_origin.assert_called_once_with(
                            mock_preprocess_report_return_value[1], mock_classified_content_value)
                        mock_merge.assert_called_once()
                        mock_add_source.assert_called_once()
                        assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
                        assert len(result["datas"]) == 1

    @staticmethod
    def test_add_source_to_report_normal_flow(source_tracer_instance, mock_preprocess_report_return_value):
        """Test normal flow of add_source_to_report."""
        with patch(
                f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin:
                mock_generate_origin.return_value = {
                    "origin_report_data": [],
                    "modified_report": "modified report"
                }

                with patch(
                        f'{MODULE_PATH}.merge_source_datas') as mock_merge:
                    mock_merge.return_value = [{"merged": "data"}]

                    with patch(
                            f'{MODULE_PATH}.add_source_references') as mock_add_source:
                        mock_add_source.return_value = (
                            "final report", [{"final": "data"}])

                        # Act
                        result = source_tracer_instance.add_source_to_report()

                        # Assert
                        mock_preprocess_report.assert_called_once()
                        mock_generate_origin.assert_called_once_with(
                            mock_preprocess_report_return_value[1],
                            getattr(source_tracer_instance, '_classified_content'))
                        mock_merge.assert_called_once()
                        mock_add_source.assert_called_once()
                        assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
                        assert len(result["datas"]) == 1

    @staticmethod
    def test_add_source_to_report_with_existing_datas(mock_algorithm_inputs, origin_search_record,
                                                      mock_preprocess_report_return_value):
        """Test add_source_to_report with existing merged_trace_source_datas."""
        # Arrange
        mock_algorithm_inputs["merged_trace_source_datas"] = [
            {"existing": "data"}]
        tracer = SourceTracer(mock_algorithm_inputs)

        with patch(
                f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search:
                mock_preprocess_search.return_value = origin_search_record

                with patch(
                        f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin:
                    mock_generate_origin.return_value = {
                        "origin_report_data": [],
                        "modified_report": "modified report"
                    }

                    with patch(
                            f'{MODULE_PATH}.merge_source_datas') as mock_merge:
                        mock_merge.return_value = [{"merged": "data"}]

                        with patch(
                                f'{MODULE_PATH}.add_source_references') as mock_add_source:
                            mock_add_source.return_value = (
                                "final report", [{"final": "data"}])

                            # Act
                            result = tracer.add_source_to_report()

                            # Assert
                            assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
                            assert len(result["datas"]) == 1
                            # Verify that existing datas are used
                            assert result["datas"] == [{"final": "data"}]

    @staticmethod
    def test_add_source_to_report_exception_handling(source_tracer_instance, origin_report_value):
        """Test add_source_to_report exception handling."""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.side_effect = Exception("Test error")

            # Act & Assert
            with pytest.raises(CustomValueException) as exc_info:
                source_tracer_instance.add_source_to_report()

            # Verify it's the expected exception type
            assert exc_info.value.error_code == StatusCode.SOURCE_TRACER_ADD_SOURCE_ERROR.code

    @staticmethod
    def test_init_with_missing_algorithm_inputs():
        """Test initialization with missing algorithm input keys."""
        # Test with completely empty dict
        tracer_empty = SourceTracer({})
        assert getattr(tracer_empty, '_report') == ""
        assert getattr(tracer_empty, '_search_record') == {}
        assert getattr(tracer_empty, '_classified_content') == []

        # Test with partial dict
        partial_inputs = {
            "report": "partial report"
            # Missing other keys
        }
        tracer_partial = SourceTracer(partial_inputs)
        assert getattr(tracer_partial, '_report') == "partial report"
        assert getattr(tracer_partial, '_search_record') == {}
        assert getattr(tracer_partial, '_classified_content') == []

    @staticmethod
    def test_init_with_none_algorithm_inputs():
        """Test initialization with None values."""
        inputs_with_nones = {
            "report": None,
            "classified_content": None
        }
        tracer = SourceTracer(inputs_with_nones)
        # Should handle None values gracefully
        assert getattr(tracer, '_report') is None
        assert getattr(tracer, '_search_record') == {}
        assert getattr(tracer, '_classified_content') is None

    @staticmethod
    def test_add_source_to_report_empty_all_trace_source_datas(mock_algorithm_inputs, origin_search_record,
                                                               mock_preprocess_report_return_value):
        """Test add_source_to_report with empty merged_trace_source_datas."""
        # Arrange
        mock_algorithm_inputs["merged_trace_source_datas"] = []
        tracer = SourceTracer(mock_algorithm_inputs)

        with patch(
                f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search:
                mock_preprocess_search.return_value = origin_search_record

                with patch(
                        f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin:
                    mock_generate_origin.return_value = {
                        "origin_report_data": [],
                        "modified_report": "modified report"
                    }

                    with patch(
                            f'{MODULE_PATH}.merge_source_datas') as mock_merge:
                        mock_merge.return_value = []

                        with patch(
                                f'{MODULE_PATH}.add_source_references') as mock_add_source:
                            mock_add_source.return_value = (
                                "final report", [])

                            # Act
                            result = tracer.add_source_to_report()

                            # Assert
                            assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
                            assert result["datas"] == []

    @staticmethod
    def test_add_source_to_report_no_datas_returned(source_tracer_instance, origin_search_record,
                                                    mock_preprocess_report_return_value):
        """Test add_source_to_report when merge_source_datas returns empty list."""
        with patch(
                f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value

            with patch(
                    f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search:
                mock_preprocess_search.return_value = origin_search_record

                with patch(
                        f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin:
                    mock_generate_origin.return_value = {
                        "origin_report_data": [],
                        "modified_report": "modified report"
                    }

                    with patch(
                            f'{MODULE_PATH}.merge_source_datas') as mock_merge:
                        mock_merge.return_value = []  # No merged data

                        with patch(
                                f'{MODULE_PATH}.add_source_references') as mock_add_source:
                            mock_add_source.return_value = (
                                "final report", [])

                            # Act
                            result = source_tracer_instance.add_source_to_report()

                            # Assert
                            assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
                            assert result["datas"] == []

    @pytest.mark.asyncio
    async def test_research_trace_source_no_datas_generated(self, source_tracer_instance, origin_search_record,
                                                            source_tracer_test_data):
        """Test research_trace_source when generate_source_datas returns empty list."""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = source_tracer_test_data.preprocess_report_return

            with patch(
                    f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search:
                mock_preprocess_search.return_value = origin_search_record

                with patch(
                        f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize:
                    mock_recognize.return_value = source_tracer_test_data.recognize_content_return

                    with patch(
                            f'{MODULE_PATH}.match_sources') as mock_match:
                        mock_match.return_value = source_tracer_test_data.match_sources_return

                        with patch(
                                f'{MODULE_PATH}.generate_source_datas') as mock_generate:
                            mock_generate.return_value = []  # No generated data

                            # Act
                            await source_tracer_instance.research_trace_source()

                            # Assert
                            assert getattr(source_tracer_instance, '_trace_source_datas') == []

    @staticmethod
    def test_transform_search_record_mixed_content():
        """
        测试当classified_content包含有效和无效字典项时，方法应只返回有效项。
        """
        classified_content = [
            {
                'url': 'http://example.com',
                'title': 'Example Title',
                'original_content': 'Example Content'
            },
            {
                'url': 'http://example2.com',
                'title': 'Example Title 2',
                # 缺少original_content字段
            },
            {
                'url': 'http://example3.com',
                'title': 'Example Title 3',
                'original_content': 'Example Content 3'
            }
        ]
        expected_result = {
            'search_record': [
                {
                    'url': 'http://example.com',
                    'title': 'Example Title',
                    'content': 'Example Content'
                },
                {
                    'url': 'http://example3.com',
                    'title': 'Example Title 3',
                    'content': 'Example Content 3'
                }
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected_result
