# -*- coding: UTF-8 -*-

"""
批次对齐逻辑的单元测试。
测试 get_source_date_mark_score 中 fast_path / LLM_path 的分批、
合并与结果对齐逻辑，确保不同路由组合下输出顺序与输入严格一致。

所有测试通过 mock 避免真实 LLM 调用。
"""

import pytest
from unittest.mock import patch, AsyncMock

from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import (
    CitationVerifyResearch,
)

MODULE_PATH = "openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research"


def _make_handle_data(
    domain="example.com",
    citation_content="",
    fact="",
    is_chart=False,
    domain_resolved=False,
    resolved_source="",
    registered_domain="",
    match_type="no_match",
    algo_marked_citation_content=None,
    algo_score=0.0,
):
    """构建 handle_data 测试辅助函数"""
    return {
        "domain": domain,
        "citation_content": citation_content,
        "fact": fact,
        "is_chart": is_chart,
        "domain_resolved": domain_resolved,
        "resolved_source": resolved_source,
        "registered_domain": registered_domain or domain,
        "match_type": match_type,
        "algo_marked_citation_content": algo_marked_citation_content or [],
        "algo_score": algo_score,
    }


class TestBatchAlignment:
    """批次分流与结果对齐测试"""

    def setup_method(self):
        self.verifier = CitationVerifyResearch("mock_model")

    # ─── 1. 10 条混合路由: 3 fast + 7 LLM → 合并后数量 & 索引对齐 ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_batch_split_and_merge_10_items(self, mock_save_mapping):
        """10 条数据: 3 条 fast (exact+resolved) + 7 条 LLM →
        merged_results 正确对齐，共 10 条结果"""
        n_total = 10
        fast_indices = [0, 3, 7]
        llm_indices = [i for i in range(n_total) if i not in fast_indices]

        self.verifier.datas = [
            {
                "url": f"https://site{i}.com/page",
                "content": f"内容条目 {i}",
                "chunk": f"事实片段 {i}",
            }
            for i in range(n_total)
        ]

        handle_datas = []
        for i in range(n_total):
            if i in fast_indices:
                handle_datas.append(
                    _make_handle_data(
                        domain=f"site{i}.com",
                        citation_content=f"内容条目 {i}",
                        fact=f"事实片段 {i}",
                        domain_resolved=True,
                        resolved_source=f"来源-{i}",
                        registered_domain=f"site{i}.com",
                        match_type="exact",
                        algo_marked_citation_content=[f"内容条目 {i}"],
                        algo_score=0.97,
                    )
                )
            else:
                handle_datas.append(
                    _make_handle_data(
                        domain=f"site{i}.com",
                        citation_content=f"内容条目 {i}",
                        fact=f"事实片段 {i}",
                        domain_resolved=False,
                        registered_domain=f"site{i}.com",
                        match_type="no_match",
                    )
                )
        handle_index = list(range(n_total))

        # LLM 路径返回与 llm_indices 数量一致的结果
        llm_results = [
            {
                "source": f"LLM来源-{i}",
                "marked_citation_content": [f"内容条目 {i}"],
                "score": 0.92,
            }
            for i in llm_indices
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

        # 总共 10 条数据应全部被处理
        assert len(self.verifier.datas) == n_total

        # 验证 fast path 结果按原始索引写入
        for i in fast_indices:
            assert self.verifier.datas[i]["source"] == f"来源-{i}", (
                f"Index {i}: fast path source incorrect"
            )
            assert self.verifier.datas[i]["score"] == 0.97

        # 验证 LLM path 结果按原始索引对齐
        for idx, orig_i in enumerate(llm_indices):
            assert self.verifier.datas[orig_i]["source"] == f"LLM来源-{orig_i}", (
                f"Index {orig_i} (llm idx {idx}): LLM source incorrect"
            )
            assert self.verifier.datas[orig_i]["score"] == 0.92

    # ─── 2. 全部走快速路径 → LLM 不被调用 ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_batch_all_exact(self, mock_save_mapping):
        """所有数据均为 exact+resolved → 全部 fast path, LLM 调用次数=0"""
        n_total = 5
        self.verifier.datas = [
            {
                "url": f"https://exact{i}.com/article",
                "content": f"精确内容 {i}",
                "chunk": f"精确事实 {i}",
            }
            for i in range(n_total)
        ]

        handle_datas = [
            _make_handle_data(
                domain=f"exact{i}.com",
                citation_content=f"精确内容 {i}",
                fact=f"精确事实 {i}",
                domain_resolved=True,
                resolved_source=f"精确来源-{i}",
                registered_domain=f"exact{i}.com",
                match_type="exact",
                algo_marked_citation_content=[f"精确内容 {i}"],
                algo_score=0.95,
            )
            for i in range(n_total)
        ]
        handle_index = list(range(n_total))

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

            await self.verifier.get_source_date_mark_score()

        # LLM 路径不应被调用
        mock_llm.assert_not_called()

        # 所有 fast path 结果正确写入
        for i in range(n_total):
            assert self.verifier.datas[i]["source"] == f"精确来源-{i}"
            assert self.verifier.datas[i]["score"] == 0.95

    # ─── 3. 全部走 LLM 路径 ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_batch_all_llm(self, mock_save_mapping):
        """所有数据均为 fuzzy/no_match/chart → 全部走 LLM 路径"""
        n_total = 6
        self.verifier.datas = [
            {
                "url": f"https://llm{i}.com/page",
                "content": f"LLM内容 {i}",
                "chunk": f"LLM事实 {i}",
            }
            for i in range(n_total)
        ]

        # 混合 match_type: fuzzy + no_match + chart
        match_types = ["fuzzy", "no_match", "chart", "fuzzy", "no_match", "chart"]
        handle_datas = [
            _make_handle_data(
                domain=f"llm{i}.com",
                citation_content=f"LLM内容 {i}",
                fact=f"LLM事实 {i}",
                is_chart=(match_types[i] == "chart"),
                domain_resolved=False,
                registered_domain=f"llm{i}.com",
                match_type=match_types[i],
            )
            for i in range(n_total)
        ]
        handle_index = list(range(n_total))

        llm_results = [
            {
                "source": f"LLM来源-{i}",
                "marked_citation_content": [f"LLM内容 {i}"],
                "score": 0.90,
            }
            for i in range(n_total)
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
            # process_batches_with_concurrency 应收到全部 6 条数据
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # LLM 路径应被调用, 且传入的 data 包含全部 6 条
        mock_llm.assert_called_once()
        call_args = mock_llm.call_args
        assert len(call_args.kwargs["data"]) == n_total

        # 所有 LLM 结果正确写入
        for i in range(n_total):
            assert self.verifier.datas[i]["source"] == f"LLM来源-{i}"
            # chart 条目 score 会被 max(score, 0.85) 处理
            if match_types[i] == "chart":
                assert self.verifier.datas[i]["score"] == max(0.90, 0.85)
            else:
                assert self.verifier.datas[i]["score"] == 0.90

    # ─── 4. batch_size=10 稳定性测试 ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_batch_size_10_stability(self, mock_save_mapping):
        """batch_size=10 时，10 条数据在单批次内处理，结果稳定且正确对齐"""
        n_total = 10
        self.verifier.verify_batch_size = 10

        self.verifier.datas = [
            {
                "url": f"https://batch{i}.com/data",
                "content": f"批次内容 {i}",
                "chunk": f"批次事实 {i}",
            }
            for i in range(n_total)
        ]

        # 全部走 LLM 路径，确保 process_batches_with_concurrency 被调用
        handle_datas = [
            _make_handle_data(
                domain=f"batch{i}.com",
                citation_content=f"批次内容 {i}",
                fact=f"批次事实 {i}",
                domain_resolved=False,
                registered_domain=f"batch{i}.com",
                match_type="no_match",
            )
            for i in range(n_total)
        ]
        handle_index = list(range(n_total))

        llm_results = [
            {
                "source": f"批次来源-{i}",
                "marked_citation_content": [f"批次内容 {i}"],
                "score": 0.91,
            }
            for i in range(n_total)
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

        # 验证 process_batches_with_concurrency 被调用且 batch_size=10
        mock_llm.assert_called_once()
        call_kwargs = mock_llm.call_args.kwargs
        assert call_kwargs["batch_size"] == 10

        # 验证 10 条结果正确对齐，无缺失或错位
        for i in range(n_total):
            assert self.verifier.datas[i]["source"] == f"批次来源-{i}", (
                f"Index {i}: source incorrect"
            )
            assert self.verifier.datas[i]["score"] == 0.91
