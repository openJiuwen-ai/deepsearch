# -*- coding: UTF-8 -*-

"""
citation_verify_research.py 分流路由逻辑的单元测试。
测试 get_source_date_mark_score 中的 fast path / LLM path 分流、
结果对齐、映射回写及 unknown source 降级等逻辑。

所有测试通过 mock 避免真实 LLM 调用。
"""

import pytest
from unittest.mock import patch, AsyncMock

from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import CitationVerifyResearch

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


class TestCitationVerifyRouting:
    """get_source_date_mark_score 分流路由测试"""

    def setup_method(self):
        self.verifier = CitationVerifyResearch("mock_model")

    # ─── 1. 快速路径 (exact + domain_resolved=True) ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_pre_routing_exact_path(self, mock_save_mapping):
        """exact match + domain_resolved=True → 走快速路径，不调 LLM"""
        self.verifier.datas = [
            {
                "url": "https://example.com/article",
                "content": "新能源汽车产业发展规划已正式发布。",
                "chunk": "新能源汽车产业发展规划",
            }
        ]

        handle_datas = [
            _make_handle_data(
                domain="example.com",
                citation_content="新能源汽车产业发展规划已正式发布。",
                fact="新能源汽车产业发展规划",
                domain_resolved=True,
                resolved_source="Example 来源",
                registered_domain="example.com",
                match_type="exact",
                algo_marked_citation_content=["新能源汽车产业发展规划"],
                algo_score=0.95,
            )
        ]
        handle_index = [0]

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare, patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_prepare.return_value = (handle_datas, handle_index)

            await self.verifier.get_source_date_mark_score()

        # LLM 路径不应被调用
        mock_llm.assert_not_called()
        # save_mapping 不应被调用 (domain_resolved=True)
        mock_save_mapping.assert_not_called()
        # 快速路径结果应写入
        assert self.verifier.datas[0]["source"] == "Example 来源"
        assert self.verifier.datas[0]["score"] == 0.95

    # ─── 2. LLM 路径 (fuzzy / no_match / domain_resolved=False) ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_pre_routing_llm_path(self, mock_save_mapping):
        """domain_resolved=False 或 match_type≠exact → 走 LLM 路径"""
        self.verifier.datas = [
            {
                "url": "https://test.org/news",
                "content": "关于人工智能发展的最新报道。",
                "chunk": "人工智能发展",
            }
        ]

        handle_datas = [
            _make_handle_data(
                domain="test.org",
                citation_content="关于人工智能发展的最新报道。",
                fact="人工智能发展动态",
                domain_resolved=False,
                registered_domain="test.org",
                match_type="fuzzy",
                algo_score=0.5,
            )
        ]
        handle_index = [0]

        llm_results = [
            {
                "source": "Test 新闻源",
                "marked_citation_content": ["人工智能发展的最新报道"],
                "score": 0.9,
            }
        ]

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare, patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_prepare.return_value = (handle_datas, handle_index)
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # LLM 路径应被调用
        mock_llm.assert_called_once()
        # LLM 结果应写入
        assert self.verifier.datas[0]["source"] == "Test 新闻源"
        assert self.verifier.datas[0]["score"] == 0.9

    # ─── 3. 图表排除 (is_chart=True → match_type="chart" → LLM 路径) ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_chart_exclusion(self, mock_save_mapping):
        """is_chart=True → match_type='chart' → 走 LLM 路径，score 下限 0.85"""
        chart_chunk = '<div style="text-align: center;">图表标题</div>'
        self.verifier.datas = [
            {
                "url": "https://chart.com/report",
                "content": chart_chunk,
                "chunk": chart_chunk,
            }
        ]

        handle_datas = [
            _make_handle_data(
                domain="chart.com",
                citation_content=chart_chunk,
                fact=chart_chunk,
                is_chart=True,
                domain_resolved=False,
                registered_domain="chart.com",
                match_type="chart",
            )
        ]
        handle_index = [0]

        llm_results = [
            {
                "source": "Chart Source",
                "marked_citation_content": [],
                "score": 0.5,
            }
        ]

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare, patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_prepare.return_value = (handle_datas, handle_index)
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # chart 走 LLM 路径
        mock_llm.assert_called_once()
        # chart 数据 source 已更新
        assert self.verifier.datas[0]["source"] == "Chart Source"
        # chart score 下限 0.85
        assert self.verifier.datas[0]["score"] == 0.85

    # ─── 4. 混合路由对齐 ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_mixed_routing_alignment(self, mock_save_mapping):
        """10 条数据: 3 条 fast (exact+resolved) + 7 条 LLM → 结果按原始索引正确对齐"""
        n_total = 10
        fast_indices = [0, 3, 7]
        llm_indices = [i for i in range(n_total) if i not in fast_indices]

        # 构建 10 条 datas
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

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare, patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_prepare.return_value = (handle_datas, handle_index)
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # 验证 fast path 结果
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

    # ─── 5. LLM 返回有效 source 时回写映射表 ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_mapping_save_on_llm_return(self, mock_save_mapping):
        """domain_resolved=False + LLM 返回有效 source → save_mapping 被调用"""
        self.verifier.datas = [
            {
                "url": "https://newsite.org/article",
                "content": "关于数字经济发展的深度分析报告。",
                "chunk": "数字经济发展报告",
            }
        ]

        handle_datas = [
            _make_handle_data(
                domain="newsite.org",
                citation_content="关于数字经济发展的深度分析报告。",
                fact="数字经济发展报告",
                domain_resolved=False,
                registered_domain="newsite.org",
                match_type="fuzzy",
            )
        ]
        handle_index = [0]

        llm_results = [
            {
                "source": "NewSite 新闻",
                "marked_citation_content": ["数字经济发展的深度分析报告"],
                "score": 0.9,
            }
        ]

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare, patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_prepare.return_value = (handle_datas, handle_index)
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # save_mapping 应被调用
        mock_save_mapping.assert_called_once_with(
            "newsite.org", "NewSite 新闻"
        )

    # ─── 6. Unknown source 降级为 domain ───

    @pytest.mark.asyncio
    @patch(f"{MODULE_PATH}.save_mapping", new_callable=AsyncMock)
    async def test_unknown_source_fallback(self, mock_save_mapping):
        """LLM 返回 'unknown source' → source 降级为 handle_data 的 domain"""
        self.verifier.datas = [
            {
                "url": "https://mystery.com/data",
                "content": "一段来源不明的数据内容。",
                "chunk": "数据内容",
            }
        ]

        handle_datas = [
            _make_handle_data(
                domain="mystery.com",
                citation_content="一段来源不明的数据内容。",
                fact="数据内容",
                domain_resolved=False,
                registered_domain="mystery.com",
                match_type="no_match",
            )
        ]
        handle_index = [0]

        llm_results = [
            {
                "source": "unknown source",
                "marked_citation_content": ["来源不明的数据内容"],
                "score": 0.9,
            }
        ]

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare, patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_prepare.return_value = (handle_datas, handle_index)
            mock_llm.return_value = llm_results

            await self.verifier.get_source_date_mark_score()

        # source 应降级为 domain
        assert self.verifier.datas[0]["source"] == "mystery.com"
        # "unknown source" 不应被保存到映射表
        mock_save_mapping.assert_not_called()

    # ─── 7. 空数据不触发任何处理 ───

    @pytest.mark.asyncio
    async def test_empty_datas_no_processing(self):
        """self.datas 为空 → 不触发任何处理, 不报错"""
        self.verifier.datas = []

        with patch.object(
            self.verifier, "prepare_handle_data", new_callable=AsyncMock
        ) as mock_prepare:
            mock_prepare.return_value = ([], [])

            await self.verifier.get_source_date_mark_score()

        assert self.verifier.datas == []


# ──────────────────────────────────────────────────────────────────────────────
# 集成测试 mock 路径
# ──────────────────────────────────────────────────────────────────────────────

LOOKUP_SOURCE_PATH = f"{MODULE_PATH}.lookup_source"
SAVE_MAPPING_PATH = f"{MODULE_PATH}.save_mapping"


class TestCitationVerifyRoutingIntegration:
    """验证快速路径分流与溯源匹配算法的端到端集成效果

    不 mock prepare_handle_data，让 classify_and_match（纯算法）直接工作，
    仅 mock lookup_source（映射表依赖外部环境）和 LLM 路径。

    测试覆盖：
    - 快速路径：种子域名 + 精确匹配 → 算法直接输出
    - LLM 路径：未知域名 → LLM 识别 → 映射回写
    - 溯源匹配算法：exact/fuzzy/no_match 对分流决策的影响
    """

    def setup_method(self):
        self.verifier = CitationVerifyResearch("mock_model")

    # ─── 1. 精确本地映射 → 快速路径 ───

    @pytest.mark.asyncio
    @patch(SAVE_MAPPING_PATH, new_callable=AsyncMock)
    @patch(LOOKUP_SOURCE_PATH, new_callable=AsyncMock)
    @patch(f"{MODULE_PATH}.LogManager")
    async def test_exact_match_known_domain_fast_path(
        self, mock_log_manager, mock_lookup, mock_save
    ):
        """种子域名命中 + 精确匹配内容 → 快速路径，LLM 不被调用

        验证：
        - lookup_source 返回 (知乎, True)
        - classify_and_match 返回 match_type=exact
        - get_source_date_mark_score 走快速路径
        - LLM 路径不被调用
        - 结果使用算法提供的 source/score
        """
        mock_log_manager.is_sensitive.return_value = False
        mock_lookup.return_value = ("知乎", True)

        citation_content = "新能源汽车充电设施建设规划正在推进中。这些措施将推动充电设施的快速普及。"
        fact = "新能源汽车充电设施建设规划正在推进中。"  # 精确子串匹配

        self.verifier.datas = [
            {
                "url": "https://zhihu.com/question/123",
                "content": citation_content,
                "chunk": fact,
            }
        ]

        with patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            await self.verifier.get_source_date_mark_score()

        # 快速路径 → LLM 不被调用
        mock_llm.assert_not_called()
        # domain_resolved=True → save_mapping 不被调用
        mock_save.assert_not_called()
        # 结果使用算法提供的值
        assert self.verifier.datas[0]["source"] == "知乎"
        assert self.verifier.datas[0]["score"] >= 0.85
        # content 应被标记（精确匹配片段）
        assert "<mark>" in self.verifier.datas[0]["content"]

    # ─── 2. 未映射域名 → LLM 路径 + 映射回写 ───

    @pytest.mark.asyncio
    @patch(SAVE_MAPPING_PATH, new_callable=AsyncMock)
    @patch(LOOKUP_SOURCE_PATH, new_callable=AsyncMock)
    @patch(f"{MODULE_PATH}.LogManager")
    async def test_unknown_domain_llm_path_with_mapping_save(
        self, mock_log_manager, mock_lookup, mock_save
    ):
        """未知域名 + 无精确匹配 → LLM 路径，LLM 返回有效 source 后 save_mapping 被调用

        验证：
        - lookup_source 返回 (domain, False)
        - classify_and_match 返回 no_match（内容不相关）
        - get_source_date_mark_score 走 LLM 路径
        - LLM 返回有效 source → save_mapping 被调用
        - 映射回写参数正确
        """
        mock_log_manager.is_sensitive.return_value = False
        # 未知域名 → lookup_source 返回未命中
        mock_lookup.return_value = ("unknown-site.org", False)

        # 完全不相关的内容 → no_match
        citation_content = "天气预报显示明天晴天，气温将回升到二十五度左右。"
        fact = "美联储降息对A股科技板块的影响分析"

        self.verifier.datas = [
            {
                "url": "https://unknown-site.org/article",
                "content": citation_content,
                "chunk": fact,
            }
        ]

        llm_results = [
            {
                "source": "TestSite 新闻",
                "marked_citation_content": ["美联储降息对A股科技板块的影响分析"],
                "score": 0.9,
            }
        ]

        with patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = llm_results
            await self.verifier.get_source_date_mark_score()

        # LLM 路径应被调用
        mock_llm.assert_called_once()
        # domain_resolved=False + LLM 返回有效 source → save_mapping 被调用
        mock_save.assert_called_once_with("unknown-site.org", "TestSite 新闻")
        # LLM 结果写入
        assert self.verifier.datas[0]["source"] == "TestSite 新闻"
        assert self.verifier.datas[0]["score"] == 0.9

    # ─── 3. 溯源匹配算法 - exact → 快速路径 ───

    @pytest.mark.asyncio
    @patch(SAVE_MAPPING_PATH, new_callable=AsyncMock)
    @patch(LOOKUP_SOURCE_PATH, new_callable=AsyncMock)
    @patch(f"{MODULE_PATH}.LogManager")
    async def test_classify_and_match_exact_routes_fast_path(
        self, mock_log_manager, mock_lookup, mock_save
    ):
        """classify_and_match 返回 exact + domain_resolved=True → 快速路径

        验证精确匹配（exact）正确影响分流决策：
        - fact 的句子是 citation_content 的精确子串
        - 覆盖率 ≥ 50% → match_type = "exact"
        - 快速路径生效 → LLM 不被调用
        """
        mock_log_manager.is_sensitive.return_value = False
        mock_lookup.return_value = ("百度", True)

        citation_content = "新能源汽车充电设施建设规划明确了未来十年的发展方向。充电设施建设是重点领域。"
        fact = "新能源汽车充电设施建设规划明确了未来十年的发展方向。"  # 精确子串，覆盖率 > 50%

        self.verifier.datas = [
            {
                "url": "https://baidu.com/article",
                "content": citation_content,
                "chunk": fact,
            }
        ]

        with patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            await self.verifier.get_source_date_mark_score()

        # exact + domain_resolved=True → 快速路径，LLM 不被调用
        mock_llm.assert_not_called()
        mock_save.assert_not_called()
        # 结果正确
        assert self.verifier.datas[0]["source"] == "百度"
        assert self.verifier.datas[0]["score"] >= 0.85

    # ─── 4. 溯源匹配算法 - fuzzy → LLM 路径 ───

    @pytest.mark.asyncio
    @patch(SAVE_MAPPING_PATH, new_callable=AsyncMock)
    @patch(LOOKUP_SOURCE_PATH, new_callable=AsyncMock)
    @patch(f"{MODULE_PATH}.LogManager")
    async def test_classify_and_match_fuzzy_routes_llm_path(
        self, mock_log_manager, mock_lookup, mock_save
    ):
        """classify_and_match 返回 fuzzy → 即使 domain_resolved=True 也走 LLM 路径

        验证模糊匹配（fuzzy）正确影响分流决策：
        - fact 句子与 citation_content 句子相似但不完全相同
        - "详细" vs "深入" → match_type = "fuzzy"
        - 即使域名已解析，fuzzy 也走 LLM 路径
        """
        mock_log_manager.is_sensitive.return_value = False
        mock_lookup.return_value = ("知乎", True)  # 域名已解析

        # 模糊匹配内容：相似但不完全相同
        citation_content = "该方案详细阐述了新能源汽车的发展规划。"
        fact = "该方案深入阐述了新能源汽车的发展规划。"  # "详细" vs "深入" → fuzzy

        self.verifier.datas = [
            {
                "url": "https://zhihu.com/question/456",
                "content": citation_content,
                "chunk": fact,
            }
        ]

        llm_results = [
            {
                "source": "知乎",
                "marked_citation_content": ["该方案详细阐述了新能源汽车的发展规划。"],
                "score": 0.85,
            }
        ]

        with patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = llm_results
            await self.verifier.get_source_date_mark_score()

        # fuzzy 即使 domain_resolved=True → LLM 路径
        mock_llm.assert_called_once()
        # domain_resolved=True → 不回写映射
        mock_save.assert_not_called()
        # LLM 结果写入
        assert self.verifier.datas[0]["source"] == "知乎"
        assert self.verifier.datas[0]["score"] == 0.85

    # ─── 5. 溯源匹配算法 - no_match → LLM 路径（转为 LLM 识别）───

    @pytest.mark.asyncio
    @patch(SAVE_MAPPING_PATH, new_callable=AsyncMock)
    @patch(LOOKUP_SOURCE_PATH, new_callable=AsyncMock)
    @patch(f"{MODULE_PATH}.LogManager")
    async def test_classify_and_match_no_match_routes_llm_path(
        self, mock_log_manager, mock_lookup, mock_save
    ):
        """classify_and_match 返回 no_match → 转为 LLM 识别，走 LLM 路径

        验证无匹配（no_match）正确影响分流决策：
        - fact 与 citation_content 完全不相关
        - match_type = "no_match"
        - 转由 LLM 进行识别判断
        - LLM 返回有效 source → 映射回写
        """
        mock_log_manager.is_sensitive.return_value = False
        mock_lookup.return_value = ("mystery.com", False)  # 域名未解析

        # 完全不相关的内容 → no_match
        citation_content = "天气预报显示明天晴天，气温将回升到二十五度左右。"
        fact = "美联储降息对A股科技板块的影响分析"

        self.verifier.datas = [
            {
                "url": "https://mystery.com/news",
                "content": citation_content,
                "chunk": fact,
            }
        ]

        llm_results = [
            {
                "source": "Mystery Site",
                "marked_citation_content": [],
                "score": 0.88,
            }
        ]

        with patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = llm_results
            await self.verifier.get_source_date_mark_score()

        # no_match + domain_resolved=False → LLM 路径
        mock_llm.assert_called_once()
        # LLM 返回有效 source → save_mapping 被调用
        mock_save.assert_called_once_with("mystery.com", "Mystery Site")
        # LLM 结果写入
        assert self.verifier.datas[0]["source"] == "Mystery Site"
        assert self.verifier.datas[0]["score"] == 0.88

    # ─── 6. unknown source 不回写映射 ───

    @pytest.mark.asyncio
    @patch(SAVE_MAPPING_PATH, new_callable=AsyncMock)
    @patch(LOOKUP_SOURCE_PATH, new_callable=AsyncMock)
    @patch(f"{MODULE_PATH}.LogManager")
    async def test_unknown_source_no_mapping_save(
        self, mock_log_manager, mock_lookup, mock_save
    ):
        """LLM 返回 'unknown source' → 不回写映射表

        验证映射回写的边界条件：
        - domain_resolved=False → 应该回写
        - 但 source = "unknown source" → 跳过回写
        """
        mock_log_manager.is_sensitive.return_value = False
        mock_lookup.return_value = ("mystery.com", False)

        citation_content = "天气预报显示明天晴天。"
        fact = "美联储降息对A股科技板块的影响分析"

        self.verifier.datas = [
            {
                "url": "https://mystery.com/news",
                "content": citation_content,
                "chunk": fact,
            }
        ]

        llm_results = [
            {
                "source": "unknown source",
                "marked_citation_content": [],
                "score": 0.88,
            }
        ]

        with patch.object(
            self.verifier,
            "process_batches_with_concurrency",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = llm_results
            await self.verifier.get_source_date_mark_score()

        mock_llm.assert_called_once()
        # "unknown source" 不应被保存到映射表
        mock_save.assert_not_called()
        # source 降级为 domain
        assert self.verifier.datas[0]["source"] == "mystery.com"
