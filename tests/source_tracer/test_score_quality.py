# -*- coding: UTF-8 -*-

"""
匹配算法分数质量的单元测试。
测试 exact_match、fuzzy_match 的 score 范围和分布一致性，
确保算法计分逻辑符合预期约束。
"""

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_match_algo import (
    classify_and_match,
    exact_match,
    fuzzy_match,
)
from openjiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences


class TestExactMatchScoreRange:
    """精确匹配分数范围测试"""

    def test_exact_match_score_range(self):
        """exact match scores 恒 >= 0.85, 最高 1.0。

        使用多组不同覆盖率的测试数据覆盖 0.85~1.0 区间。
        """
        test_cases = [
            # (citation_content, fact, 描述)
            (
                "新能源汽车产业发展规划明确了 future 十年的发展方向。",
                "新能源汽车产业发展规划明确了 future 十年的发展方向。",
                "全覆盖 → score=1.0",
            ),
            (
                "新能源汽车充电设施建设正在推进中。充电设施在全国范围快速普及。",
                "新能源汽车充电设施建设正在推进中。其他不相关内容完全无法匹配的文字。",
                "部分覆盖(约50%) → score≥0.85",
            ),
            (
                "推进绿色低碳发展是国家重要战略方向之一。",
                "推进绿色低碳发展是国家重要战略方向之一",
                "无标点差异全覆盖 → score=1.0",
            ),
        ]

        for citation, fact, desc in test_cases:
            matched, score = exact_match(citation, fact)
            if matched:
                assert score >= 0.85, f"[{desc}] score {score} < 0.85"
                assert score <= 1.0, f"[{desc}] score {score} > 1.0"


class TestFuzzyMatchScoreRange:
    """模糊匹配分数范围测试"""

    def test_fuzzy_match_score_range(self):
        """fuzzy match scores 在 [0.3, 0.7] 之间。

        有命中时下限 0.3，上限 0.7。无命中时返回 0.0。
        """
        test_cases = [
            (
                "该方案详细阐述了新能源汽车的发展规划。",
                "该方案深入阐述了新能源汽车的发展规划。",
                "高相似度模糊匹配",
            ),
            (
                "中国经济保持了稳定的增长态势，GDP增速维持在合理区间。",
                "国内经济呈现出平稳增长的良好势头，GDP增幅保持在适度范围。",
                "中等相似度模糊匹配",
            ),
            (
                "人工智能技术在自然语言处理方面取得了显著进展。深度学习模型性能不断提升。",
                "AI技术在文本理解领域有了重要突破。神经网络模型效果持续改善。",
                "多句模糊匹配",
            ),
        ]

        for citation, fact, desc in test_cases:
            matched, score = fuzzy_match(citation, fact)
            if matched:
                assert 0.3 <= score <= 0.7, (
                    f"[{desc}] fuzzy score {score} not in [0.3, 0.7]"
                )
            else:
                assert score == 0.0, (
                    f"[{desc}] no match should give score=0.0, got {score}"
                )


class TestScoreDistributionComparable:
    """分数分布一致性测试：已知数据的算法分数在预期值 ±5% 范围内"""

    def test_score_distribution_comparable(self):
        """对已知测试数据，算法分数落在预期值的 ±5% 范围内。

        计算公式:
          exact:  score = 0.85 + coverage_rate × 0.15
          fuzzy:  score = coverage_rate × 0.7 (clamped to [0.3, 0.7])
        """
        # ─── exact match 测试 ───
        # 全覆盖: coverage=1.0 → expected = 0.85 + 1.0×0.15 = 1.0
        citation_full = "新能源汽车产业发展规划明确了未来发展方向。"
        fact_full = "新能源汽车产业发展规划明确了未来发展方向。"
        _, score_exact_full = exact_match(citation_full, fact_full)
        expected_full = 1.0
        assert abs(score_exact_full - expected_full) <= expected_full * 0.05, (
            f"exact full coverage: expected ~{expected_full}, got {score_exact_full}"
        )

        # ─── fuzzy match 测试 ───
        # 高相似度：几乎一致的文字 → 覆盖率接近 1.0
        # expected ≈ 1.0 × 0.7 = 0.7 (capped at 0.7)
        citation_fuzzy = "该方案详细阐述了新能源汽车的发展规划。"
        fact_fuzzy = "该方案深入阐述了新能源汽车的发展规划。"
        _, score_fuzzy = fuzzy_match(citation_fuzzy, fact_fuzzy)
        # 单句高相似覆盖，coverage=1.0 → score = min(1.0*0.7, 0.7) = 0.7
        expected_fuzzy = 0.7
        assert abs(score_fuzzy - expected_fuzzy) <= expected_fuzzy * 0.05, (
            f"fuzzy high similarity: expected ~{expected_fuzzy}, got {score_fuzzy}"
        )

        # ─── classify_and_match 路由一致性 ───
        # exact 路由
        match_type_e, _, score_e = classify_and_match(citation_full, fact_full)
        assert match_type_e == "exact"
        assert abs(score_e - expected_full) <= expected_full * 0.05

        # fuzzy 路由
        match_type_f, _, score_f = classify_and_match(citation_fuzzy, fact_fuzzy)
        assert match_type_f == "fuzzy"
        assert abs(score_f - expected_fuzzy) <= expected_fuzzy * 0.05
