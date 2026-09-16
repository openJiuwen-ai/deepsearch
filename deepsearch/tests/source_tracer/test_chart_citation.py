# -*- coding: UTF-8 -*-

"""
图表引用路径的单元测试。
测试 is_chart / is_vlm_chart 的分流路由、图表 score 下限 0.85、
以及 VLM 图表 algo_marked_citation_content 为空的逻辑。

所有测试通过 mock 避免真实 LLM 调用。
"""

import pytest
from unittest.mock import patch, AsyncMock

from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import (
    CitationVerifyResearch,
)

MODULE_PATH = "openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research"


class TestChartCitationLlmPath:
    """图表引用走 LLM 路径测试"""

    def setup_method(self):
        self.verifier = CitationVerifyResearch("mock_model")

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_chart_citation_llm_path(self, mock_save_mapping):
        """is_chart=True → match_type='chart' → 走 LLM 路径，非 fast path。

        即使 domain_resolved=True，由于 match_type != 'exact'，
        仍应路由到 LLM 路径。
        """
        chart_chunk = '<div style="text-align: center;">图表标题</div>'
        self.verifier.datas = [
            {
                "url": "https://chart.com/report",
                "content": chart_chunk,
                "chunk": chart_chunk,
            }
        ]

        # 模拟 prepare_handle_data 返回:
        # match_type="chart" + domain_resolved=True → 仍非 fast path
        handle_datas = [
            {
                "domain": "chart.com",
                "citation_content": chart_chunk,
                "fact": chart_chunk,
                "is_chart": True,
                "domain_resolved": True,  # 即使已解析
                "resolved_source": "Chart Source Cached",
                "registered_domain": "chart.com",
                "match_type": "chart",  # 非 exact → 不走 fast path
                "algo_marked_citation_content": [],
                "algo_score": 0.0,
            }
        ]
        handle_index = [0]

        llm_results = [
            {
                "source": "LLM Chart Source",
                "marked_citation_content": [],
                "score": 0.6,
            }
        ]

        with (
            patch.object(
                self.verifier, "prepare_handle_data", new_callable=AsyncMock
            ) as mock_prepare,
            patch.object(
                self.verifier,
                "process_batches_with_concurrency",
                new_callable=AsyncMock,
            ) as mock_llm,
        ):
            mock_prepare.return_value = (handle_datas, handle_index)
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # chart 必须走 LLM 路径
        mock_llm.assert_called_once()
        # 验证传入的数据量为 1（即 chart 项被路由到 LLM）
        call_kwargs = mock_llm.call_args.kwargs
        assert len(call_kwargs["data"]) == 1
        # 验证最终数据: chart score 下限 0.85
        assert self.verifier.datas[0]["score"] == 0.85
        # source 应来自 LLM 结果
        assert self.verifier.datas[0]["source"] == "LLM Chart Source"


class TestChartScoreMinimum:
    """图表 score 下限 0.85 测试"""

    def setup_method(self):
        self.verifier = CitationVerifyResearch("mock_model")

    def test_chart_score_min_0_85(self):
        """update_citation_data: is_chart=True → score = max(result_score, 0.85)。

        即使 LLM 返回的 score 低于 0.85，图表 score 也应被提升到 0.85。
        """
        self.verifier.datas = [
            {"content": "图表内容描述", "valid": True, "score": 0.0},
        ]
        handle_index = [0]

        # handle_datas 中标记 is_chart=True
        handle_datas = [
            {
                "domain": "chart.com",
                "citation_content": "图表内容",
                "fact": "图表事实",
                "is_chart": True,
                "registered_domain": "chart.com",
            }
        ]

        # LLM 返回低分
        ordered_results = [
            {
                "source": "Chart Source",
                "marked_citation_content": [],
                "score": 0.3,  # 低于 0.85
            }
        ]

        self.verifier.update_citation_data(handle_index, ordered_results, handle_datas)

        # chart score 应被 max(0.3, 0.85) = 0.85
        assert self.verifier.datas[0]["score"] == 0.85
        assert self.verifier.datas[0]["source"] == "Chart Source"

    def test_chart_terminal_failure_keeps_existing_fallback(self):
        """A chart extraction failure remains renderable with the 0.85 fallback."""
        self.verifier.datas = [{"content": "图表内容", "valid": True, "score": 0.0}]

        self.verifier.update_citation_data(
            [0],
            [{"extract_failed_reason": "parse"}],
            [{
                "domain": "chart.com",
                "citation_content": "图表内容",
                "fact": "图表事实",
                "is_chart": True,
            }],
        )

        assert self.verifier.datas[0]["valid"] is True
        assert self.verifier.datas[0]["source"] == "chart.com"
        assert self.verifier.datas[0]["score"] == 0.85

    def test_chart_score_above_threshold_preserved(self):
        """update_citation_data: is_chart=True, result_score > 0.85 → 保留原分数"""
        self.verifier.datas = [
            {"content": "图表描述", "valid": True, "score": 0.0},
        ]
        handle_index = [0]

        handle_datas = [
            {
                "domain": "chart.com",
                "citation_content": "图表内容",
                "fact": "图表事实",
                "is_chart": True,
                "registered_domain": "chart.com",
            }
        ]

        ordered_results = [
            {
                "source": "Chart Source",
                "marked_citation_content": [],
                "score": 0.95,
            }
        ]

        self.verifier.update_citation_data(handle_index, ordered_results, handle_datas)

        # score 高于 0.85 时保留原值
        assert self.verifier.datas[0]["score"] == 0.95

    def test_chart_score_string_number(self):
        """update_citation_data: LLM 返回字符串数字 score，safe_float 正确转换。

        LLM JSON 可能返回 "0.3" 而非 0.3，safe_float 应正确转换，
        不应 TypeError 崩溃。
        """
        self.verifier.datas = [
            {"content": "图表内容描述", "valid": True, "score": 0.0},
        ]
        handle_index = [0]

        handle_datas = [
            {
                "domain": "chart.com",
                "citation_content": "图表内容",
                "fact": "图表事实",
                "is_chart": True,
                "registered_domain": "chart.com",
            }
        ]

        # LLM 返回字符串数字
        ordered_results = [
            {
                "source": "Chart Source",
                "marked_citation_content": [],
                "score": "0.3",  # 字符串数字，低于 0.85
            }
        ]

        self.verifier.update_citation_data(handle_index, ordered_results, handle_datas)

        # safe_float("0.3") = 0.3, max(0.3, 0.85) = 0.85
        assert self.verifier.datas[0]["score"] == 0.85

    def test_chart_score_string_number_above_threshold(self):
        """update_citation_data: 字符串数字 score 高于 0.85 时正确保留。"""
        self.verifier.datas = [
            {"content": "图表描述", "valid": True, "score": 0.0},
        ]
        handle_index = [0]

        handle_datas = [
            {
                "domain": "chart.com",
                "citation_content": "图表内容",
                "fact": "图表事实",
                "is_chart": True,
                "registered_domain": "chart.com",
            }
        ]

        ordered_results = [
            {
                "source": "Chart Source",
                "marked_citation_content": [],
                "score": "0.95",  # 字符串数字，高于 0.85
            }
        ]

        self.verifier.update_citation_data(handle_index, ordered_results, handle_datas)

        # safe_float("0.95") = 0.95, max(0.95, 0.85) = 0.95
        assert self.verifier.datas[0]["score"] == 0.95


class TestVlmChartNoMarkedContent:
    """VLM 图表 algo_marked_citation_content 为空测试"""

    def setup_method(self):
        self.verifier = CitationVerifyResearch("mock_model")

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.lookup_source", new_callable=AsyncMock)
    async def test_vlm_chart_no_marked_content(self, mock_lookup):
        """is_vlm_chart=True → match_type='chart',
        algo_marked_citation_content=[], algo_score=0.0。

        VLM 图表跳过 classify_and_match，直接走 chart 路径。
        """
        mock_lookup.return_value = ("VLM Source", True)

        self.verifier.datas = [
            {
                "url": "https://vlm-chart.com/data",
                "content": "VLM 图表描述内容",
                "chunk": "VLM 图表事实",
                "is_vlm_chart": True,
            }
        ]

        handle_datas, handle_index = await self.verifier.prepare_handle_data()

        assert len(handle_datas) == 1
        hd = handle_datas[0]

        # match_type 必须是 "chart"
        assert hd["match_type"] == "chart"
        # algo_marked_citation_content 必须为空列表
        assert hd["algo_marked_citation_content"] == []
        # algo_score 必须为 0.0
        assert hd["algo_score"] == 0.0
