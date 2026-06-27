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
            "classified_content": origin_search_record.get("web_page_search_record", []),
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
            "name": "",
            "url": "",
            "title": "example",
            "content": "test content",
            "source": "",
            "publish_time": "",
            "from": "",
            "chunk": "test",
            "score": 0.0,
            "id": "",
        }
        return [data]

    @pytest.fixture
    def mock_classified_content_value(self):
        return [{"index": 1, "title": "example", "url": "https://example.com", "original_content": "test content"}]

    # ========== research_trace_source tests ==========

    @pytest.mark.asyncio
    async def test_research_trace_source_empty_report(self):
        """report为空时，research_trace_source应直接返回且不修改trace_source_datas。"""
        tracer = SourceTracer({"report": "", "classified_content": []})
        await tracer.research_trace_source()
        assert getattr(tracer, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_empty_classified_content(self):
        """classified_content为空时，搜索记录为空，research_trace_source应退出溯源。"""
        tracer = SourceTracer({"report": "有内容的报告", "classified_content": []})
        await tracer.research_trace_source()
        assert getattr(tracer, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_recognition_failure(self, source_tracer_instance):
        """内容识别失败时，trace_source_datas保持为空列表。"""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess, \
             patch(f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search, \
             patch(f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize:
            mock_preprocess.return_value = ("", "预处理后的报告")
            mock_preprocess_search.return_value = {"search_record": [{"url": "https://a.com", "title": "A", "content": "内容"}]}
            mock_recognize.return_value = []

            await source_tracer_instance.research_trace_source()
            assert getattr(source_tracer_instance, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_matching_failure(self, source_tracer_instance):
        """源匹配失败时，trace_source_datas保持为空列表。"""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess, \
             patch(f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search, \
             patch(f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize, \
             patch(f'{MODULE_PATH}.match_sources') as mock_match:
            mock_preprocess.return_value = ("", "预处理后的报告")
            mock_preprocess_search.return_value = {"search_record": [{"url": "https://a.com", "title": "A", "content": "内容"}]}
            mock_recognize.return_value = ["测试内容"]
            mock_match.return_value = []

            await source_tracer_instance.research_trace_source()
            assert getattr(source_tracer_instance, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_no_datas_generated(self, source_tracer_instance,
                                                            origin_search_record):
        """generate_source_datas返回空列表时，trace_source_datas保持为空列表。"""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess, \
             patch(f'{MODULE_PATH}.preprocess_search_record') as mock_preprocess_search, \
             patch(f'{MODULE_PATH}.recognize_content_to_cite') as mock_recognize, \
             patch(f'{MODULE_PATH}.match_sources') as mock_match, \
             patch(f'{MODULE_PATH}.generate_source_datas') as mock_generate:
            mock_preprocess.return_value = ("", "预处理后的报告")
            mock_preprocess_search.return_value = origin_search_record
            mock_recognize.return_value = ["测试内容"]
            mock_match.return_value = [{"sentence": "test", "matched_source_indices": [1]}]
            mock_generate.return_value = []

            await source_tracer_instance.research_trace_source()
            assert getattr(source_tracer_instance, '_trace_source_datas') == []

    @pytest.mark.asyncio
    async def test_research_trace_source_exception_handling(self, source_tracer_instance):
        """research_trace_source异常处理：应抛出CustomValueException。"""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.side_effect = Exception("Test error")

            with pytest.raises(CustomValueException) as exc_info:
                await source_tracer_instance.research_trace_source()

            assert exc_info.value.error_code == StatusCode.SOURCE_TRACER_TRACE_SOURCE_ERROR.code

    # ========== add_source_to_report tests ==========

    @staticmethod
    def test_add_source_to_report_with_classified_content(mock_algorithm_inputs,
                                                          mock_classified_content_value,
                                                          mock_preprocess_report_return_value):
        """有classified_content时，add_source_to_report正常执行引用合并和添加。"""
        mock_algorithm_inputs["classified_content"] = mock_classified_content_value
        tracer = SourceTracer(mock_algorithm_inputs)

        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report, \
             patch(f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin, \
             patch(f'{MODULE_PATH}.merge_source_datas') as mock_merge, \
             patch(f'{MODULE_PATH}.add_source_references') as mock_add_source:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value
            mock_generate_origin.return_value = {
                "origin_report_data": [{"chunk": "Existing reference", "_sentence_position": 10}],
                "modified_report": "modified report"
            }
            mock_merge.return_value = [{"merged": "data"}]
            mock_add_source.return_value = ("final report", [{"final": "data"}])

            result = tracer.add_source_to_report()

            mock_preprocess_report.assert_called_once()
            mock_generate_origin.assert_called_once_with(
                mock_preprocess_report_return_value[1], mock_classified_content_value)
            mock_merge.assert_called_once()
            mock_add_source.assert_called_once()
            assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
            assert len(result["datas"]) == 1

    @staticmethod
    def test_add_source_to_report_no_datas_returned(source_tracer_instance,
                                                     mock_preprocess_report_return_value):
        """merge_source_datas返回空列表时，报告仍正常返回但datas为空。"""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess_report, \
             patch(f'{MODULE_PATH}.generate_origin_report_data') as mock_generate_origin, \
             patch(f'{MODULE_PATH}.merge_source_datas') as mock_merge, \
             patch(f'{MODULE_PATH}.add_source_references') as mock_add_source:
            mock_preprocess_report.return_value = mock_preprocess_report_return_value
            mock_generate_origin.return_value = {
                "origin_report_data": [],
                "modified_report": "modified report"
            }
            mock_merge.return_value = []
            mock_add_source.return_value = ("final report", [])

            result = source_tracer_instance.add_source_to_report()

            assert result["modified_report"] == "final report" + mock_preprocess_report_return_value[0]
            assert result["datas"] == []

    @staticmethod
    def test_add_source_to_report_exception_handling(source_tracer_instance):
        """add_source_to_report异常处理：应抛出CustomValueException。"""
        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.side_effect = Exception("Test error")

            with pytest.raises(CustomValueException) as exc_info:
                source_tracer_instance.add_source_to_report()

            assert exc_info.value.error_code == StatusCode.SOURCE_TRACER_ADD_SOURCE_ERROR.code

    # ========== 初始化测试 ==========

    @staticmethod
    def test_init_with_missing_algorithm_inputs():
        """缺少算法输入键时，SourceTracer应使用默认值。"""
        tracer_empty = SourceTracer({})
        assert getattr(tracer_empty, '_report') == ""
        assert getattr(tracer_empty, '_search_record') == {}
        assert getattr(tracer_empty, '_classified_content') == []

        tracer_partial = SourceTracer({"report": "partial report"})
        assert getattr(tracer_partial, '_report') == "partial report"
        assert getattr(tracer_partial, '_search_record') == {}
        assert getattr(tracer_partial, '_classified_content') == []

    @staticmethod
    def test_init_with_none_algorithm_inputs():
        """传入None值时，SourceTracer应安全处理。"""
        tracer = SourceTracer({"report": None, "classified_content": None})
        assert getattr(tracer, '_report') is None
        assert getattr(tracer, '_search_record') == {}
        assert getattr(tracer, '_classified_content') is None

    # ========== transform_search_record 测试 ==========

    @staticmethod
    def test_transform_search_record_mixed_content():
        """classified_content包含有效和无效项时，应只返回有效项。"""
        classified_content = [
            {'url': 'http://example.com', 'title': 'Example Title', 'original_content': 'Example Content'},
            {'url': 'http://example2.com', 'title': 'Example Title 2'},  # 缺少original_content
            {'url': 'http://example3.com', 'title': 'Example Title 3', 'original_content': 'Example Content 3'}
        ]
        expected_result = {
            'search_record': [
                {'url': 'http://example.com', 'title': 'Example Title', 'content': 'Example Content'},
                {'url': 'http://example3.com', 'title': 'Example Title 3', 'content': 'Example Content 3'}
            ]
        }
        result = SourceTracer.transform_search_record(classified_content)
        assert result == expected_result

    # ========== _filter_meaningful_sentences 测试 ==========

    @staticmethod
    def test_filter_meaningful_sentences_removes_empty_and_structural():
        """过滤空字符串、Markdown标题、表格分隔行、代码块标记，保留表格数据行。"""
        sentences = [
            "",           # 空字符串
            "  ",         # 仅空白
            "## 第一章",  # Markdown标题
            "### 1.1 小节",  # Markdown标题
            "|---|---|",  # 表格分隔行（应过滤）
            "| 数据1 | 数据2 |",  # 表格数据行（应保留，可能含引用）
            "```python",  # 代码块开始标记
            "```",        # 代码块结束标记
            "这是一段正文内容。",  # 正常句子
            "另一段有意义的内容！",  # 正常句子
        ]
        result = SourceTracer._filter_meaningful_sentences(sentences)
        assert result == ["| 数据1 | 数据2 |", "这是一段正文内容。", "另一段有意义的内容！"]

    # ========== _calculate_coverage 测试 ==========

    @staticmethod
    def test_calculate_coverage_with_citations():
        """报告含引用标记时，正确计算被覆盖句子数和覆盖率。"""
        report = "第一句[citation:1]。第二句。第三句[citation:2]。"
        coverage, covered_count = SourceTracer._calculate_coverage(report)
        assert covered_count == 2
        assert coverage == pytest.approx(2 / 3)

    @staticmethod
    def test_calculate_coverage_no_citations():
        """报告无引用标记时，覆盖率为0。"""
        report = "第一句。第二句。第三句。"
        coverage, covered_count = SourceTracer._calculate_coverage(report)
        assert covered_count == 0
        assert coverage == 0.0

    @staticmethod
    def test_calculate_coverage_empty_report():
        """空报告时，覆盖率为0，被覆盖句子数为0。"""
        coverage, covered_count = SourceTracer._calculate_coverage("")
        assert covered_count == 0
        assert coverage == 0.0

    @staticmethod
    def test_calculate_coverage_filters_structural_before_counting():
        """覆盖率计算时，Markdown标题和表格行不计入有意义句子总数。"""
        report = "## 第一章\n正文[citation:1]。"
        coverage, covered_count = SourceTracer._calculate_coverage(report)
        # 过滤后只有"正文[citation:1]。"有意义，1/1=1.0
        assert covered_count == 1
        assert coverage == 1.0

    # ========== pre_check_origin_coverage tests ==========

    @staticmethod
    def test_pre_check_origin_coverage_empty_report():
        """空报告时，不需要生成溯源，reason为'empty report'。"""
        tracer = SourceTracer({"report": "", "classified_content": []})
        result = tracer.pre_check_origin_coverage()

        assert result["need_generate"] is False
        assert result["origin_count"] == 0
        assert result["total_sentences"] == 0
        assert result["coverage"] == 0.0
        assert result["reason"] == "empty report"

    @staticmethod
    def test_pre_check_origin_coverage_no_classified_content():
        """有报告但无classified_content时，无法生成新引用，跳过溯源。"""
        tracer = SourceTracer({"report": "报告内容", "classified_content": []})
        result = tracer.pre_check_origin_coverage()

        assert result["need_generate"] is False
        assert result["reason"] == "no classified content"
        assert result["origin_count"] == 0
        assert result["total_sentences"] == 0

    def test_pre_check_origin_coverage_sufficient_coverage(self):
        """被覆盖句子数>=10且覆盖率>=0.3时，跳过溯源生成。

        13个有意义句子中11个带[citation:X]标记 → covered_count=11, coverage≈0.846。
        """
        tracer = SourceTracer({
            "report": "原始报告",
            "classified_content": [{"url": "https://a.com", "title": "文章A", "original_content": "内容A"}]
        })

        preprocessed_report = (
            "第一句[citation:1]。第二句[citation:2]。第三句[citation:3]。"
            "第四句[citation:4]。第五句[citation:5]。第六句[citation:6]。"
            "第七句[citation:7]。第八句[citation:8]。第九句[citation:9]。"
            "第十句[citation:10]。第十一句[citation:11]。第十二句。第十三句。"
        )

        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = ("参考文献章节", preprocessed_report)

            result = tracer.pre_check_origin_coverage()

            assert result["need_generate"] is False
            assert result["reason"] == "coverage sufficient"
            assert result["origin_count"] == 11
            assert result["total_sentences"] == 13
            assert result["coverage"] == pytest.approx(11 / 13)

    def test_pre_check_origin_coverage_insufficient_origin_count(self):
        """被覆盖句子数不足（<10）时，需要执行溯源生成。

        8个有意义句子均带引用 → covered_count=8, coverage=1.0，
        但 origin_count=8 < 10 → 需要生成溯源。
        """
        tracer = SourceTracer({
            "report": "原始报告",
            "classified_content": [{"url": "https://a.com", "title": "文章A", "original_content": "内容A"}]
        })

        preprocessed_report = (
            "第一句[citation:1]。第二句[citation:2]。第三句[citation:3]。"
            "第四句[citation:4]。第五句[citation:5]。第六句[citation:6]。"
            "第七句[citation:7]。第八句[citation:8]。"
        )

        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = ("参考文献章节", preprocessed_report)

            result = tracer.pre_check_origin_coverage()

            assert result["need_generate"] is True
            assert result["reason"] == "coverage insufficient"
            assert result["origin_count"] == 8
            assert result["total_sentences"] == 8
            assert result["coverage"] == 1.0

    def test_pre_check_origin_coverage_insufficient_coverage_ratio(self):
        """覆盖率比例不足（<0.3）时，需要执行溯源生成。

        5个有意义句子中仅1个带引用标记 → covered_count=1, coverage=0.2。
        """
        tracer = SourceTracer({
            "report": "原始报告",
            "classified_content": [{"url": "https://a.com", "title": "文章A", "original_content": "内容A"}]
        })

        preprocessed_report = "第一句[citation:1]。第二句。第三句。第四句。第五句。"

        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = ("参考文献章节", preprocessed_report)

            result = tracer.pre_check_origin_coverage()

            assert result["need_generate"] is True
            assert result["reason"] == "coverage insufficient"
            assert result["origin_count"] == 1
            assert result["total_sentences"] == 5
            assert result["coverage"] == pytest.approx(0.2)

    def test_pre_check_origin_coverage_no_citations(self):
        """报告中有意义句子但无任何引用标记时，需要执行溯源生成。

        所有句子均无引用 → covered_count=0, coverage=0.0。
        """
        tracer = SourceTracer({
            "report": "原始报告",
            "classified_content": [{"url": "https://a.com", "title": "文章A", "original_content": "内容A"}]
        })

        preprocessed_report = "第一句。第二句。第三句。第四句。第五句。"

        with patch(f'{MODULE_PATH}.preprocess_report') as mock_preprocess:
            mock_preprocess.return_value = ("参考文献章节", preprocessed_report)

            result = tracer.pre_check_origin_coverage()

            assert result["need_generate"] is True
            assert result["reason"] == "coverage insufficient"
            assert result["origin_count"] == 0
            assert result["total_sentences"] == 5
            assert result["coverage"] == 0.0
