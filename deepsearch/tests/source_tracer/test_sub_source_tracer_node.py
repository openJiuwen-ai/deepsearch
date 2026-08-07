# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_tracer import SourceTracer
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    SubSourceTracerNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ChapterSidecar, SubReportContent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


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
                "web_page_search_record": [
                    {"title": "example", "url": "https://example.com", "content": "test content"}],
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
        """Test _do_invoke method with trace source enabled.

        覆盖率不足场景：_pre_check_origin_coverage 返回 need_generate=True，
        触发 research_trace_source 调用。
        不 mock __init__，让 SourceTracer 正常初始化以确保 _min_origin_coverage_count 等属性存在。
        """
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

        # Mock 覆盖率不足 → 执行溯源生成
        insufficient_check_result = {
            "need_generate": True,
            "origin_count": 1,
            "total_sentences": 3,
            "coverage": 0.33,
            "reason": "coverage insufficient",
        }
        expected_add_source_result = {
            "modified_report": "Test modified report",
            "datas": [{"id": "test_id", "content": "Test content"}]}

        # 不 mock __init__，让 SourceTracer 正常初始化
        with patch.object(SourceTracer, 'pre_check_origin_coverage') as mock_pre_check:
            mock_pre_check.return_value = insufficient_check_result
            with patch.object(SourceTracer, 'research_trace_source', new_callable=AsyncMock) as mock_research:
                with patch.object(SourceTracer, 'add_source_to_report') as mock_add_source:
                    mock_add_source.return_value = expected_add_source_result

                    with patch.object(sub_source_tracer_node, 'post_handle') as mock_post_handle:
                        mock_post_handle.return_value = {
                            "next_node": NodeId.END.value}

                        # Act
                        result = await sub_source_tracer_node.do_invoke(None, mock_session, None)

                    # Assert
                    mock_pre_check.assert_called_once()
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

    @pytest.mark.asyncio
    async def test_do_invoke_skips_generated_citations_when_switch_disabled(
            self, sub_source_tracer_node, mock_session, mock_search_context):
        """Test _do_invoke skips new citation generation when the fine-grained switch is disabled."""
        sub_report_content_obj = SubReportContent(
            sub_report_content_text=mock_search_context["sub_report_content"],
            classified_content=[]
        )

        def get_global_state_side_effect(key):
            if key == "config.source_tracer_research_trace_source_switch":
                return True
            elif key == "config.source_tracer_generated_citation_switch":
                return False
            elif key == "section_context.sub_report_content":
                return sub_report_content_obj
            elif key == "section_context.search_record":
                return mock_search_context["search_record"]
            elif key == "section_context.language":
                return mock_search_context["language"]
            return None

        mock_session.get_global_state.side_effect = get_global_state_side_effect

        expected_add_source_result = {
            "modified_report": "Test modified report",
            "datas": [{"id": "test_id", "content": "Test content"}]}
        with patch.object(SourceTracer, '__init__', return_value=None) as mock_init:
            with patch.object(SourceTracer, 'research_trace_source', new_callable=AsyncMock) as mock_research:
                with patch.object(SourceTracer, 'add_source_to_report') as mock_add_source:
                    mock_add_source.return_value = expected_add_source_result

                    with patch.object(sub_source_tracer_node, 'post_handle') as mock_post_handle:
                        mock_post_handle.return_value = {
                            "next_node": NodeId.END.value}

                        result = await sub_source_tracer_node.do_invoke(None, mock_session, None)

                    mock_init.assert_called_once()
                    mock_research.assert_not_called()
                    mock_add_source.assert_called_once()
                    mock_post_handle.assert_called_once()

                    args, kwargs = mock_post_handle.call_args
                    algorithm_output = args[1]
                    assert algorithm_output["trace_source_datas"] == expected_add_source_result["datas"]
                    assert algorithm_output["modified_report"] == expected_add_source_result["modified_report"]
                    assert result == {"next_node": NodeId.END.value}

    @staticmethod
    def test_post_handle(sub_source_tracer_node, mock_session):
        """Test post_handle method with different scenarios."""
        # Mock get_global_state to return SubReportContent object
        existing_sub_report = SubReportContent(
            sub_report_content_text="Original content",
            classified_content=[],
            sub_report_chapter_sidecar=ChapterSidecar(chapter_summary="Structured summary"),
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
        assert updated_sub_report.sub_report_chapter_sidecar.chapter_summary == "Structured summary"

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

    @staticmethod
    def test_post_handle_logs_article_link_follow_final_reference_outcomes(
        sub_source_tracer_node,
        mock_session,
        caplog,
    ):
        traced_url = "https://example.com/traced-followed"
        missing_url = "https://example.com/missing-followed"
        existing_sub_report = SubReportContent(
            sub_report_content_text="Original content",
            classified_content=[
                {
                    "url": traced_url,
                    "source_id": "traced-source",
                    "discovery": {"method": "article_link_follow", "depth": 1},
                },
                {
                    "url": missing_url,
                    "source_id": "missing-source",
                    "discovery": {"method": "article_link_follow", "depth": 1},
                },
            ],
        )

        def get_global_state(key):
            if key == "section_context.sub_report_content":
                return existing_sub_report
            if key == "section_context.section_idx":
                return 2
            return None

        mock_session.get_global_state.side_effect = get_global_state
        algorithm_output = {
            "trace_source_datas": [{"source_id": "traced-source"}],
            "modified_report": "Final report without literal followed URLs.",
        }

        with caplog.at_level(logging.INFO):
            result = sub_source_tracer_node.post_handle(
                None,
                algorithm_output,
                mock_session,
                None,
            )

        assert result["next_node"] == NodeId.END.value
        assert any(
            "phase=report_final_reference" in message
            and traced_url in message
            and "outcome=cited" in message
            and "trace_source_match=true" in message
            for message in caplog.messages
        )
        assert any(
            "phase=report_final_reference" in message
            and missing_url in message
            and "outcome=not_cited" in message
            for message in caplog.messages
        )

    # ========== 覆盖率预检查集成测试（Commit f2381df 性能优化） ==========

    @pytest.mark.asyncio
    async def test_do_invoke_coverage_sufficient_skip_research_trace(
        self, sub_source_tracer_node, mock_session
    ):
        """覆盖率充足时，跳过耗时的溯源生成步骤（research_trace_source）。

        场景：_pre_check_origin_coverage 返回 need_generate=False，
        则 research_trace_source() 不应被调用，但仍执行 add_source_to_report()。
        """
        pre_handle_inputs = {
            "report": "包含引用的报告文本",
            "classified_content": [
                {"url": "https://a.com", "title": "来源A", "original_content": "内容A"},
            ],
            "research_trace_source_switch": True,
            "generated_citation_switch": True,
            "language": "zh-CN",
            "llm_model_name": "mock_model",
            "section_idx": 2,
        }

        sufficient_check_result = {
            "need_generate": False,
            "origin_count": 11,
            "total_sentences": 13,
            "coverage": 0.846,
            "reason": "coverage sufficient",
        }

        add_source_result = {
            "modified_report": "保留原文引用的报告",
            "datas": [{"id": "origin_1", "content": "原始引用数据"}],
        }

        mock_session.get_global_state.return_value = None

        with patch.object(sub_source_tracer_node, 'pre_handle') as mock_pre_handle, \
             patch.object(SourceTracer, 'pre_check_origin_coverage') as mock_pre_check, \
             patch.object(SourceTracer, 'research_trace_source', new_callable=AsyncMock) as mock_research, \
             patch.object(SourceTracer, 'add_source_to_report') as mock_add_source, \
             patch.object(sub_source_tracer_node, 'post_handle') as mock_post_handle:

            mock_pre_handle.return_value = pre_handle_inputs
            mock_pre_check.return_value = sufficient_check_result
            mock_add_source.return_value = add_source_result
            mock_post_handle.return_value = {"next_node": NodeId.END.value}

            # 执行
            await sub_source_tracer_node.do_invoke(None, mock_session, None)

            # 验证：pre_check_origin_coverage 被调用
            mock_pre_check.assert_called_once()

            # 验证：research_trace_source 未被调用（跳过耗时步骤）
            mock_research.assert_not_called()

            # 验证：add_source_to_report 仍被调用（保留原文引用）
            mock_add_source.assert_called_once()

            # 验证：post_handle 输出正确
            mock_post_handle.assert_called_once()
            args, kwargs = mock_post_handle.call_args
            algorithm_output = args[1]
            assert algorithm_output["modified_report"] == "保留原文引用的报告"
            assert algorithm_output["trace_source_datas"] == [{"id": "origin_1", "content": "原始引用数据"}]

    @pytest.mark.asyncio
    async def test_do_invoke_coverage_insufficient_execute_research_trace(
        self, sub_source_tracer_node, mock_session
    ):
        """覆盖率不足时，正常执行溯源生成步骤（research_trace_source）。

        场景：_pre_check_origin_coverage 返回 need_generate=True，
        则 research_trace_source() 应被调用，生成新增引用。
        """
        pre_handle_inputs = {
            "report": "引用覆盖率不足的报告文本",
            "classified_content": [
                {"url": "https://a.com", "title": "来源A", "original_content": "内容A"},
            ],
            "research_trace_source_switch": True,
            "generated_citation_switch": True,
            "language": "zh-CN",
            "llm_model_name": "mock_model",
            "section_idx": 3,
        }

        insufficient_check_result = {
            "need_generate": True,
            "origin_count": 8,
            "total_sentences": 8,
            "coverage": 1.0,
            "reason": "coverage insufficient",
        }

        add_source_result = {
            "modified_report": "添加溯源引用后的完整报告",
            "datas": [
                {"id": "origin_1", "content": "原始引用"},
                {"id": "generated_1", "content": "新增溯源引用"},
            ],
        }

        mock_session.get_global_state.return_value = None

        with patch.object(sub_source_tracer_node, 'pre_handle') as mock_pre_handle, \
             patch.object(SourceTracer, 'pre_check_origin_coverage') as mock_pre_check, \
             patch.object(SourceTracer, 'research_trace_source', new_callable=AsyncMock) as mock_research, \
             patch.object(SourceTracer, 'add_source_to_report') as mock_add_source, \
             patch.object(sub_source_tracer_node, 'post_handle') as mock_post_handle:

            mock_pre_handle.return_value = pre_handle_inputs
            mock_pre_check.return_value = insufficient_check_result
            mock_add_source.return_value = add_source_result
            mock_post_handle.return_value = {"next_node": NodeId.END.value}

            # 执行
            await sub_source_tracer_node.do_invoke(None, mock_session, None)

            # 验证：pre_check_origin_coverage 被调用
            mock_pre_check.assert_called_once()

            # 验证：research_trace_source 被调用（执行溯源生成）
            mock_research.assert_called_once()

            # 验证：add_source_to_report 被调用
            mock_add_source.assert_called_once()

            # 验证：post_handle 输出包含完整溯源数据
            mock_post_handle.assert_called_once()
            args, kwargs = mock_post_handle.call_args
            algorithm_output = args[1]
            assert algorithm_output["modified_report"] == "添加溯源引用后的完整报告"
            assert len(algorithm_output["trace_source_datas"]) == 2
