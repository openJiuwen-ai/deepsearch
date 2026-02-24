# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from jiuwen_deepsearch.framework.jiuwen.agent.reasoning_writing_graph.editor_team_nodes import SubSourceTracerNode
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import SubReportContent
from jiuwen_deepsearch.algorithm.source_trace.source_tracer import SourceTracer
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class ExposedSubSourceTracerNode(SubSourceTracerNode):
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


class TestSubSourceTracerNode:
    """Test cases for SubSourceTracerNode class."""

    @pytest.fixture
    def sub_source_tracer_node(self):
        """Fixture to create a SubSourceTracerNode instance."""
        return ExposedSubSourceTracerNode()

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
            "sub_report_content": "This is a test sub report content.",
            "search_record": {
                "web_page_search_record": [{"title": "example", "url": "https://example.com", "content": "test content"}],
                "web_image_search_record": [],
                "local_text_search_record": [],
                "local_image_search_record": []
            },
            "language": "zh-CN"
        }

    @staticmethod
    def test_pre_handle(sub_source_tracer_node, mock_session, mock_search_context):
        """Test _pre_handle method with different configurations."""
        # Test with trace source enabled
        sub_report_content_obj = SubReportContent(
            sub_report_content_text=mock_search_context["sub_report_content"],
            classified_content=[]
        )

        def get_global_state_side_effect_enabled(key):
            if key == "config.source_tracer_research_trace_source_switch":
                return True
            elif key == "section_context.sub_report_content":
                return sub_report_content_obj
            elif key == "section_context.search_record":
                return mock_search_context["search_record"]
            elif key == "section_context.language":
                return mock_search_context["language"]
            return None

        mock_session.get_global_state.side_effect = get_global_state_side_effect_enabled
        result = sub_source_tracer_node.pre_handle(None, mock_session, None)
        assert result["research_trace_source_switch"] is True
        assert result["report"] == mock_search_context["sub_report_content"]
        assert result["language"] == mock_search_context["language"]

        # Test with trace source disabled
        def get_global_state_side_effect_disabled(key):
            if key == "config.source_tracer_research_trace_source_switch":
                return False
            return get_global_state_side_effect_enabled(key)

        mock_session.get_global_state.side_effect = get_global_state_side_effect_disabled
        result = sub_source_tracer_node.pre_handle(None, mock_session, None)
        assert result["research_trace_source_switch"] is False

    @staticmethod
    def test_skip_trace_source_handle(sub_source_tracer_node, mock_session):
        """Test _skip_trace_source_handle method."""
        with patch.object(sub_source_tracer_node, 'post_handle') as mock_post_handle:
            mock_post_handle.return_value = {"next_node": NodeId.END.value}
            current_inputs = {"report": "Test report"}
            result = sub_source_tracer_node.skip_trace_source_handle(
                None, mock_session, None, current_inputs)

            mock_post_handle.assert_called_once()
            args, kwargs = mock_post_handle.call_args
            algorithm_output = args[1]
            assert algorithm_output["trace_source_datas"] == []
            assert algorithm_output["modified_report"] == current_inputs["report"]

    @pytest.mark.asyncio
    async def test_do_invoke_with_trace_enabled(self, sub_source_tracer_node, mock_session, mock_search_context):
        """Test _do_invoke method with trace source enabled."""
        # Setup mock
        sub_report_content_obj = SubReportContent(
            sub_report_content_text=mock_search_context["sub_report_content"],
            classified_content=[]
        )

        def get_global_state_side_effect(key):
            if key == "config.source_tracer_research_trace_source_switch":
                return True
            elif key == "section_context.sub_report_content":
                return sub_report_content_obj
            elif key == "section_context.search_record":
                return mock_search_context["search_record"]
            elif key == "section_context.language":
                return mock_search_context["language"]
            return None

        mock_session.get_global_state.side_effect = get_global_state_side_effect

        # Mock SourceTracer
        expected_result = {
            "datas": [{"id": "test_id", "content": "Test content"}]}
        expected_add_source_result = {
            "modified_report": "Test modified report",
            "datas": [{"id": "test_id", "content": "Test content"}]}
        with patch.object(SourceTracer, '__init__', return_value=None) as mock_init:
            with patch.object(SourceTracer, 'research_trace_source', new_callable=AsyncMock) as mock_research:
                mock_research.return_value = expected_result
                with patch.object(SourceTracer, 'add_source_to_report') as mock_add_source:
                    mock_add_source.return_value = expected_add_source_result

                    with patch.object(sub_source_tracer_node, 'post_handle') as mock_post_handle:
                        mock_post_handle.return_value = {
                            "next_node": NodeId.END.value}

                        # Act
                        result = await sub_source_tracer_node.do_invoke(None, mock_session, None)

                    # Assert
                    mock_init.assert_called_once()
                    mock_research.assert_called_once()
                    mock_add_source.assert_called_once()
                    mock_post_handle.assert_called_once()

                    # Check that the algorithm_output passed to post_handle contains the expected data
                    args, kwargs = mock_post_handle.call_args
                    algorithm_output = args[1]
                    assert algorithm_output["trace_source_datas"] == expected_add_source_result["datas"]
                    assert algorithm_output["modified_report"] == expected_add_source_result["modified_report"]

    @pytest.mark.asyncio
    async def test_do_invoke_with_trace_disabled(self, sub_source_tracer_node, mock_session, mock_search_context):
        """Test _do_invoke method with trace source disabled."""
        # Setup mock
        sub_report_content_obj = SubReportContent(
            sub_report_content_text=mock_search_context["sub_report_content"],
            classified_content=[]
        )

        def get_global_state_side_effect(key):
            if key == "config.source_tracer_research_trace_source_switch":
                return False
            elif key == "section_context.sub_report_content":
                return sub_report_content_obj
            elif key == "section_context.search_record":
                return mock_search_context["search_record"]
            elif key == "section_context.language":
                return mock_search_context["language"]
            return None

        mock_session.get_global_state.side_effect = get_global_state_side_effect

        with patch.object(sub_source_tracer_node, 'skip_trace_source_handle') as mock_skip:
            mock_skip.return_value = {"next_node": NodeId.END.value}

            # Act
            result = await sub_source_tracer_node.do_invoke(None, mock_session, None)

            # Assert
            mock_skip.assert_called_once()

    @staticmethod
    def test_post_handle(sub_source_tracer_node, mock_session):
        """Test post_handle method with different scenarios."""
        # Mock get_global_state to return SubReportContent object
        existing_sub_report = SubReportContent(
            sub_report_content_text="Original content",
            classified_content=[]
        )

        def get_global_state_side_effect(key):
            if key == "section_context.sub_report_content":
                return existing_sub_report
            elif key == "section_context.section_idx":
                return 1
            return None
        
        mock_session.get_global_state.side_effect = get_global_state_side_effect
        
        # Test with trace source datas
        algorithm_output = {
            "trace_source_datas": [
                {"id": "test_id", "content": "Test content",
                    "url": "https://example.com"}
            ],
            "modified_report": "Test modified report"
        }

        result = sub_source_tracer_node.post_handle(
            None, algorithm_output, mock_session, None)

        # Assert
        assert result["next_node"] == NodeId.END.value
        mock_session.update_global_state.assert_called_once()
        call_args = mock_session.update_global_state.call_args[0][0]
        assert "section_context.sub_report_content" in call_args
        updated_sub_report = call_args["section_context.sub_report_content"]
        assert isinstance(updated_sub_report, SubReportContent)
        assert updated_sub_report.sub_report_content_text == "Test modified report"
        assert updated_sub_report.sub_report_trace_source_datas == algorithm_output["trace_source_datas"]

        # Test with empty trace source datas
        mock_session.reset_mock()
        existing_sub_report = SubReportContent(
            sub_report_content_text="Original content",
            classified_content=[]
        )
        mock_session.get_global_state.side_effect = get_global_state_side_effect
        algorithm_output = {"trace_source_datas": [], "modified_report": ""}

        result = sub_source_tracer_node.post_handle(
            None, algorithm_output, mock_session, None)

        # Assert
        assert result["next_node"] == NodeId.END.value
        mock_session.update_global_state.assert_called_once()
        call_args = mock_session.update_global_state.call_args[0][0]
        assert "section_context.sub_report_content" in call_args
        updated_sub_report = call_args["section_context.sub_report_content"]
        assert isinstance(updated_sub_report, SubReportContent)
        assert updated_sub_report.sub_report_content_text == ""
        assert updated_sub_report.sub_report_trace_source_datas == []     
