# -*- coding: UTF-8 -*-

"""
source_match_algo 模块的单元测试。
测试精确匹配、模糊匹配、分类路由、分句及标点处理等纯算法逻辑。
"""

import pytest

from openjiuwen_deepsearch.algorithm.source_trace.source_match_algo import (
    classify_and_match,
    exact_match,
    fuzzy_match,
    _strip_trailing_punct,
)
from openjiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences


class TestExactMatchBasic:
    """精确匹配基础测试"""

    def test_exact_match_basic(self):
        """exact_match: fact 句子是 citation_content 的子串 → 匹配成功, score ≥ 0.85"""
        citation_content = (
            "新能源汽车充电设施建设规划中提到了一系列新的政策措施。"
            "这些措施将推动充电设施在全国范围内的快速普及。"
        )
        fact = "新能源汽车充电设施建设规划。这些措施将推动充电设施在全国范围内的快速普及。"

        matched, score = exact_match(citation_content, fact)

        assert len(matched) >= 1
        assert score >= 0.85
        # 所有匹配的句子应该来自 fact
        for s in matched:
            assert _strip_trailing_punct(s) in citation_content

    def test_exact_match_full_coverage(self):
        """exact_match: 全覆盖 → score == 1.0"""
        citation_content = "推进新能源汽车产业发展是当前重要战略。"
        fact = "推进新能源汽车产业发展"

        matched, score = exact_match(citation_content, fact)

        assert len(matched) == 1
        assert score == pytest.approx(1.0)


class TestFuzzyMatchBasic:
    """模糊匹配基础测试"""

    def test_fuzzy_match_basic(self):
        """classify_and_match: 相似但不完全相同的文本 → fuzzy, score ∈ [0.3, 0.7]"""
        citation_content = "该方案详细阐述了新能源汽车的发展规划。"
        fact = "该方案深入阐述了新能源汽车的发展规划。"

        match_type, matched, score = classify_and_match(citation_content, fact)

        assert match_type == "fuzzy"
        assert len(matched) >= 1
        assert 0.3 <= score <= 0.7

    def test_fuzzy_match_via_fuzzy_match_func(self):
        """fuzzy_match 直接调用: 有命中时 score ∈ [0.3, 0.7]"""
        citation_content = "该方案详细阐述了新能源汽车的发展规划。"
        fact = "该方案深入阐述了新能源汽车的发展规划。"

        matched, score = fuzzy_match(citation_content, fact)

        assert len(matched) >= 1
        assert 0.3 <= score <= 0.7


class TestNoMatchBasic:
    """无匹配测试"""

    def test_no_match_basic(self):
        """classify_and_match: 完全无关的文本 → no_match, score=0.0"""
        citation_content = "天气预报显示明天晴天，气温将回升到二十五度左右。"
        fact = "美联储降息对A股科技板块的影响分析"

        match_type, matched, score = classify_and_match(citation_content, fact)

        assert match_type == "no_match"
        assert len(matched) == 0
        assert score == 0.0


class TestClassifyAndMatchRouting:
    """classify_and_match 路由测试: exact → fuzzy → no_match 分支覆盖"""

    def test_route_to_exact(self):
        """精确内容 → match_type='exact'"""
        citation_content = "新能源汽车充电设施建设规划中提到了一系列新的政策措施。"
        fact = "新能源汽车充电设施建设规划中提到了一系列新的政策措施。"

        match_type, matched, score = classify_and_match(citation_content, fact)

        assert match_type == "exact"
        assert score >= 0.85

    def test_route_to_fuzzy(self):
        """相似内容但不精确匹配 → match_type='fuzzy'"""
        citation_content = "该方案详细阐述了新能源汽车的发展规划。"
        fact = "该方案深入阐述了新能源汽车的发展规划。"

        match_type, matched, score = classify_and_match(citation_content, fact)

        assert match_type == "fuzzy"
        assert 0.3 <= score <= 0.7

    def test_route_to_no_match(self):
        """无重叠内容 → match_type='no_match'"""
        citation_content = "人工智能技术在医疗领域的应用"
        fact = "区块链在供应链金融中的创新实践"

        match_type, matched, score = classify_and_match(citation_content, fact)

        assert match_type == "no_match"
        assert score == 0.0


class TestEmptyInputHandling:
    """空输入边界测试"""

    def test_empty_citation_content(self):
        """citation_content 为空 → no_match"""
        match_type, matched, score = classify_and_match("", "这是一段事实内容")
        assert match_type == "no_match"
        assert matched == []
        assert score == 0.0

    def test_empty_fact(self):
        """fact 为空 → no_match"""
        match_type, matched, score = classify_and_match("引用内容在这里", "")
        assert match_type == "no_match"
        assert matched == []
        assert score == 0.0

    def test_both_empty(self):
        """两个都为空 → no_match"""
        match_type, matched, score = classify_and_match("", "")
        assert match_type == "no_match"
        assert score == 0.0

    def test_exact_match_empty_input(self):
        """exact_match 空输入 → ([], 0.0)"""
        matched, score = exact_match("", "fact")
        assert matched == []
        assert score == 0.0

    def test_fuzzy_match_empty_input(self):
        """fuzzy_match 空输入 → ([], 0.0)"""
        matched, score = fuzzy_match("", "fact")
        assert matched == []
        assert score == 0.0


class TestChineseSentenceSplitting:
    """中文标点分句测试"""

    def test_split_four_sentences(self):
        """四种标点分隔 → 4 个句子"""
        result = split_into_sentences("第一句话。第二句话！第三句话？第四句话")
        assert len(result) == 4

    def test_split_with_all_punctuations(self):
        """中英文标点混合分句（split_into_sentences 不以英文句号.分句）"""
        result = split_into_sentences("句子一。句子二.句子三！句子四!句子五？句子六?句子七；句子八;句子九")
        # split_into_sentences 不在英文句号 . 处切分，"句子二.句子三" 不被拆开
        assert len(result) == 8

    def test_split_no_punctuation(self):
        """无标点文本 → 单元素列表"""
        result = split_into_sentences("无标点的文本")
        assert result == ["无标点的文本"]

    def test_split_empty_input(self):
        """空输入 → 空列表"""
        result = split_into_sentences("")
        assert result == []

    def test_split_punctuation_preserved(self):
        """分句后标点保留在句子末尾"""
        result = split_into_sentences("中国是最大的发展中国家。人口众多。")
        assert result == ["中国是最大的发展中国家。", "人口众多。"]

    def test_english_period_between_chinese_chars_no_split(self):
        """英文句点在中文文字之间不会被切分（split_into_sentences 不以.为分隔符）"""
        result = split_into_sentences("这是一句话.这是另一句话")
        assert len(result) == 1

class TestScoreRange:
    """分数范围验证"""

    def test_exact_score_minimum(self):
        """exact match 最低 score = 0.85 (部分匹配，coverage > 0.5)"""
        # fact 有 2 句，其中 1 句精确匹配 → coverage ≈ 0.5+
        citation_content = "新能源汽车充电设施建设规划正在推进中。"
        fact = "新能源汽车充电设施建设规划。这是完全不相关的内容，无法匹配。"

        matched, score = exact_match(citation_content, fact)

        if matched:  # 有匹配时 score ≥ 0.85
            assert score >= 0.85

    def test_no_match_score_zero(self):
        """no_match → score = 0.0"""
        match_type, _, score = classify_and_match(
            "人工智能在金融风控中的应用",
            "区块链去中心化技术的底层原理"
        )
        assert match_type == "no_match"
        assert score == 0.0

    def test_fuzzy_score_range(self):
        """fuzzy match → score ∈ [0.3, 0.7]"""
        citation_content = "该方案详细阐述了新能源汽车的发展规划。"
        fact = "该方案深入阐述了新能源汽车的发展规划。"

        match_type, _, score = classify_and_match(citation_content, fact)

        if match_type == "fuzzy":
            assert 0.3 <= score <= 0.7


class TestMarkedCitationContentExtraction:
    """标记引用内容提取测试"""

    def test_exact_match_returns_matching_sentences(self):
        """exact_match 返回的 matched 是 fact 的句子（在 citation_content 中命中）"""
        citation_content = "新能源汽车产业发展规划明确了未来十年的发展方向。充电设施建设是重点。"
        fact = "新能源汽车产业发展规划明确了未来十年的发展方向。其他不相关内容。"

        matched, score = exact_match(citation_content, fact)

        assert len(matched) >= 1
        # 匹配的句子来自 fact
        assert "新能源汽车产业发展规划明确了未来十年的发展方向" in matched[0]

    def test_no_false_positive_match(self):
        """无关内容不应产生匹配"""
        citation_content = "人工智能技术在图像识别领域取得了重大突破。"
        fact = "区块链技术改变了金融行业的运作方式。"

        matched, score = exact_match(citation_content, fact)

        assert matched == []


class TestExactMatchPunctuationBoundary:
    """精确匹配标点边界测试"""

    def test_trailing_punct_difference_still_matches(self):
        """fact 句子末尾有标点、citation_content 无标点 → 仍能匹配"""
        citation_content = "新能源汽车充电设施建设规划正在推进"
        fact = "新能源汽车充电设施建设规划正在推进。"

        matched, score = exact_match(citation_content, fact)

        assert len(matched) == 1
        assert score == pytest.approx(1.0)

    def test_citation_has_trailing_punct_fact_does_not(self):
        """citation_content 有标点、fact 无标点 → 仍能匹配"""
        citation_content = "新能源汽车充电设施建设规划正在推进中。"
        fact = "新能源汽车充电设施建设规划正在推进中"

        matched, score = exact_match(citation_content, fact)

        assert len(matched) == 1
        assert score == pytest.approx(1.0)

    def test_strip_trailing_punct_function(self):
        """_strip_trailing_punct 正确剥离各类尾部标点"""
        assert _strip_trailing_punct("测试文本。") == "测试文本"
        assert _strip_trailing_punct("测试文本？") == "测试文本"
        assert _strip_trailing_punct("测试文本！") == "测试文本"
        assert _strip_trailing_punct("测试文本；") == "测试文本"
        assert _strip_trailing_punct("测试文本，") == "测试文本"
        assert _strip_trailing_punct("测试文本.") == "测试文本"
        assert _strip_trailing_punct("无标点") == "无标点"
