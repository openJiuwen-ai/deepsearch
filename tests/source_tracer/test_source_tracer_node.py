# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from jiuwen_deepsearch.framework.jiuwen.agent.main_graph_nodes import SourceTracerNode
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Report


class ExposedSourceTracerNode(SourceTracerNode):
    """用于测试的类，公开受保护的方法以遵循 G.CLS.11 规则"""

    def _pre_handle(self, *args, **kwargs):
        return self.pre_handle(*args, **kwargs)

    def pre_handle(self, *args, **kwargs):
        return super()._pre_handle(*args, **kwargs)

    def _skip_trace_source_handle(self, *args, **kwargs):
        return self.skip_trace_source_handle(*args, **kwargs)

    def skip_trace_source_handle(self, *args, **kwargs):
        return super()._skip_trace_source_handle(*args, **kwargs)

    async def _do_invoke(self, *args, **kwargs):
        return await self.do_invoke(*args, **kwargs)

    async def do_invoke(self, *args, **kwargs):
        return await super()._do_invoke(*args, **kwargs)

    def _post_handle(self, *args, **kwargs):
        return self.post_handle(*args, **kwargs)

    def post_handle(self, *args, **kwargs):
        return super()._post_handle(*args, **kwargs)


class TestSourceTracerNode:
    """Test cases for SourceTracerNode class."""

    @pytest.fixture
    def source_tracer_node(self):
        """Fixture to create a SourceTracerNode instance."""
        return ExposedSourceTracerNode()

    @pytest.fixture
    def mock_session(self):
        """Fixture to create a mock Session instance."""
        session = MagicMock()
        session.get_global_state = MagicMock()
        session.update_global_state = MagicMock()
        return session

    @pytest.fixture
    def mock_search_context(self):
        """Fixture to provide mock search context data."""
        return {
            "search_mode": "research",
            "current_report": Report(
                id="test_report_id",
                report_content="This is a test report with some content."
            ),
            "search_record": {
                "web_page_search_record": [
                    {"title": "example", "url": "https://example.com", "content": "test content"}],
                "web_image_search_record": [],
                "local_text_search_record": [],
                "local_image_search_record": []
            },
            "merged_trace_source_datas": [],
            "all_classified_contents": [],
            "language": "zh-CN"
        }

    @pytest.fixture
    def mock_config(self):
        """Fixture to provide mock config data."""
        return {
            "source_tracer_research_trace_source_switch": True
        }

    @staticmethod
    def test_pre_handle(source_tracer_node, mock_session, mock_search_context, mock_config):
        """Test _pre_handle method with different configurations."""

        # Test with research mode and trace source enabled
        def get_global_state_side_effect_enabled(key):
            if key == "search_context.search_mode":
                return mock_search_context["search_mode"]
            elif key == "search_context.current_report":
                return mock_search_context["current_report"]
            elif key == "config.source_tracer_research_trace_source_switch":
                return mock_config["source_tracer_research_trace_source_switch"]
            elif key in ["search_context.search_record", "search_context.merged_trace_source_datas",
                         "search_context.all_classified_contents", "search_context.language"]:
                return mock_search_context.get(key.replace("search_context.", ""))
            return None

        mock_session.get_global_state.side_effect = get_global_state_side_effect_enabled
        result = source_tracer_node.pre_handle(None, mock_session, None)
        assert result["need_exit"] is False
        assert result["search_mode"] == "research"
        assert result["research_trace_source_switch"] is True

        # Test with research mode and trace source disabled
        def get_global_state_side_effect_disabled(key):
            if key == "config.source_tracer_research_trace_source_switch":
                return False
            return get_global_state_side_effect_enabled(key)

        mock_session.get_global_state.side_effect = get_global_state_side_effect_disabled
        result = source_tracer_node.pre_handle(None, mock_session, None)
        assert result["need_exit"] is True

    @pytest.mark.asyncio
    async def test_build_citation_checker_result(self, source_tracer_node):
        """Test build_citation_checker_result static method."""
        # Arrange
        citation_checker_info = {
            "need_check": True,
            "response_content": {
                "language": "zh-CN",
                "article": "Article with references"
            }
        }

        datas = [{"id": "test_id", "content": "Test content"}]
        expected_citation_result = json.dumps({
            "response_content": "Processed report",
            "citation_messages": {"test_id": "Valid"}
        }, ensure_ascii=False)

        with patch('jiuwen_deepsearch.framework.jiuwen.agent.main_graph_nodes.postprocess_by_citation_checker',
                   new_callable=AsyncMock) as mock_postprocess:
            mock_postprocess.return_value = expected_citation_result

            # Act
            result = await SourceTracerNode.build_citation_checker_result(citation_checker_info, datas, "mock_model")

            # Assert
            mock_postprocess.assert_called_once_with(
                citation_checker_info["response_content"], datas, "mock_model")
            assert result["check_result"] is True
            assert result["citation_checker_result_str"] == expected_citation_result

    @pytest.mark.asyncio
    async def test_do_invoke(self, source_tracer_node, mock_session, mock_search_context, mock_config):
        """Test _do_invoke method with different scenarios."""

        # Setup common mock
        def get_global_state_side_effect(key):
            if key == "search_context":
                return mock_search_context
            elif key == "config":
                return mock_config
            return None

        mock_session.get_global_state.side_effect = get_global_state_side_effect

        mock_source_tracer_result = ("Report with citations", [
            {"id": "test_id", "content": "Test content"}])
        mock_citation_checker_info = {
            "need_check": True, "response_content": {"article": "Test article"}}
        mock_citation_checker_result = {
            "check_result": True, "citation_checker_result_str": '{"response_content": "Valid"}'}

        with patch.object(source_tracer_node, 'pre_handle') as mock_pre_handle:
            with patch('jiuwen_deepsearch.framework.jiuwen.agent.main_graph_nodes.preprocess_info') as mock_preprocess:
                with patch.object(SourceTracerNode, 'build_citation_checker_result',
                                  new_callable=AsyncMock) as mock_build_citation:
                    with patch.object(source_tracer_node, 'post_handle') as mock_post_handle:
                        # Test normal flow
                        mock_pre_handle.return_value = {
                            "need_exit": False, "report": "test", "language": "zh-CN"}
                        mock_preprocess.return_value = mock_citation_checker_info
                        mock_build_citation.return_value = mock_citation_checker_result
                        mock_post_handle.return_value = {
                            "next_node": "END"}

                        await source_tracer_node.do_invoke(None, mock_session, None)
                        mock_build_citation.assert_called_once()

                        # Test need_exit flow
                        mock_pre_handle.return_value = {"need_exit": True}
                        with patch.object(source_tracer_node, 'skip_trace_source_handle') as mock_skip:
                            mock_skip.return_value = {"next_node": "END"}
                            await source_tracer_node.do_invoke(None, mock_session, None)
                            mock_skip.assert_called_once()

                        # Test exception handling
                        mock_pre_handle.return_value = {
                            "need_exit": False, "report": "test"}
                        # 模拟preprocess_info抛出异常，触发异常处理分支
                        mock_preprocess.side_effect = Exception("Test exception")
                        await source_tracer_node.do_invoke(None, mock_session, None)
                        # Verify exception was handled by checking post_handle was called with check_result: False
                        args, kwargs = mock_post_handle.call_args
                        algorithm_output = args[1]
                        assert algorithm_output["check_result_dict"]["check_result"] is False
