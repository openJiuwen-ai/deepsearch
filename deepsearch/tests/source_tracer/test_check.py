from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.checker import (
    preprocess_info,
    postprocess_by_citation_checker
)
from openjiuwen_deepsearch.common.exception import CustomException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.common.common_constants import ENGLISH, CHINESE

pytest_plugins = ["pytest_asyncio"]


class TestPreprocessInfo:
    """Test cases for preprocess_info function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.checker.logger')
    def test_preprocess_info_empty_report(self, mock_logger):
        """Test preprocessing with empty report (both empty string and None)."""
        # Test with empty string
        result_empty = preprocess_info("", ["data1", "data2"], CHINESE)
        assert result_empty["need_check"] is False

        # Verify logger was called for both cases
        assert mock_logger.warning.call_count == 1
        mock_logger.warning.assert_any_call(
            "CitationChecker: empty report, skipped citation check."
        )

    @patch('openjiuwen_deepsearch.algorithm.source_trace.checker.logger')
    def test_preprocess_info_empty_datas(self, mock_logger):
        """Test preprocessing with empty datas."""
        result = preprocess_info("Test report", [], CHINESE)

        assert result["need_check"] is False
        mock_logger.warning.assert_called_once_with(
            "CitationChecker: empty datas means no inline citation in the text, skipped citation check."
        )

    def test_preprocess_info_language_handling(self):
        """Test that language is handled correctly without normalization."""
        report = "Test report\n\n# 参考文章\n 这是一个参考文章。"
        datas = ["data1", "data2"]

        # Test Chinese language
        result_zh = preprocess_info(report, datas, CHINESE)
        assert result_zh["need_check"] is True
        assert result_zh["response_content"]["language"] == CHINESE
        assert "# 参考文章" in result_zh["response_content"]["article"]
        expected_article_zh = "Test report\n\n# 参考文章"
        assert result_zh["response_content"]["article"] == expected_article_zh

        # Test English language
        report = "Test report\n\n# Reference Articles\n article"
        result_en = preprocess_info(report, datas, ENGLISH)
        assert result_en["need_check"] is True
        assert result_en["response_content"]["language"] == ENGLISH
        assert "# Reference Articles" in result_en["response_content"]["article"]
        expected_article_en = "Test report\n\n# Reference Articles"
        assert result_en["response_content"]["article"] == expected_article_en


class TestPostprocessByCitationChecker:
    """Test cases for postprocess_by_citation_checker function."""

    @patch('openjiuwen_deepsearch.algorithm.source_trace.checker.CitationCheckerResearch')
    @pytest.mark.asyncio
    async def test_postprocess_success(self, mock_checker_class):
        """Test successful postprocessing by citation checker."""
        # Setup mock
        mock_checker = AsyncMock()
        mock_checker_class.return_value = mock_checker
        mock_checker.checker.return_value = '{"status": "success", "data": "processed_data"}'

        report = {"language": CHINESE, "article": "Test article"}
        datas = ["data1", "data2"]

        result = await postprocess_by_citation_checker(report, datas, "mock_model")

        assert result == '{"status": "success", "data": "processed_data"}'
        mock_checker.checker.assert_called_once_with(report, datas)

    @patch('openjiuwen_deepsearch.algorithm.source_trace.checker.CitationCheckerResearch')
    @pytest.mark.asyncio
    async def test_postprocess_checker_exception(self, mock_checker_class):
        """Test postprocessing when citation checker raises an exception."""
        # Setup mock to raise exception
        mock_checker = AsyncMock()
        mock_checker_class.return_value = mock_checker
        mock_checker.checker.side_effect = Exception("Checker error")

        report = {"language": "en", "article": "Test article"}
        datas = ["data1", "data2"]

        with pytest.raises(CustomException) as exc_info:
            await postprocess_by_citation_checker(report, datas, "mock_model")

        assert exc_info.value.error_code == StatusCode.CITATION_CHECKER_POST_PROCESS_ERROR.code
