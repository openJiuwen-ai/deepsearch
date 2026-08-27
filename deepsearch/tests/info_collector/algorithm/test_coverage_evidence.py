# -*- coding: UTF-8 -*-
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    CoverageOptions,
    CoveragePassage,
    _coverage_features,
    _coverage_score,
    _count_entities,
    exclude_passages,
    extract_coverage_passages,
)

#: 无任何事实特征（不触发数字/日期/时间/实体/引用），用于制造间隔。
_FILLER = "本报告基于公开资料整理，供内部参考使用。"


def _fact_heavy_document() -> str:
    """构造一个事实密度参差的文档，用于校验抽取优先级。"""
    return "\n\n".join(
        [
            _FILLER,
            "OpenAI于2025年3月发布新模型，推理速度提升50%，定价99美元/月。",
            "该模型主要面向企业用户，首月已覆盖3万家客户，覆盖12个国家。",
            "公司宣布将在2026年投入5亿元用于扩大海外市场布局。",
            _FILLER,
            "目录",
        ]
    )


def test_extract_coverage_passages_returns_evidence_blocks():
    result = extract_coverage_passages(_fact_heavy_document())

    assert isinstance(result, list)
    assert all(isinstance(item, CoveragePassage) for item in result)
    assert all(item.source_indices for item in result)
    assert all(item.text for item in result)


def test_extract_coverage_passages_prioritizes_fact_density_over_position():
    result = extract_coverage_passages(_fact_heavy_document())

    assert result, "expected at least one coverage block"
    selected_text = "\n".join(item.text for item in result)
    assert "2025年3月发布新模型" in selected_text
    assert "3万家客户" in selected_text
    assert _FILLER not in selected_text
    assert "目录" not in selected_text


def test_extract_coverage_passages_returns_empty_for_junk_content():
    assert extract_coverage_passages("") == []
    assert extract_coverage_passages("   \n\n  ") == []
    assert extract_coverage_passages("目录\n上一页") == []
    assert extract_coverage_passages("这是一段不含任何数字日期实体引用的普通描述文字，通篇都是叙述性内容。") == []


def test_normalize_preserves_fullwidth_punctuation_and_merges_blank_lines():
    content = "2025年，公司发布了新产品。\n\n\n\n该产品定价99元。\r\n"
    result = extract_coverage_passages(content)

    assert result
    block_text = result[0].text
    # 全角标点必须原样保留，不能被 NFKC 折叠成半角。
    assert "2025年，" in block_text
    assert "。" in block_text


def test_normalize_strips_html_and_control_chars():
    content = "<div>2025年营收增长18%</div>\r\n\r\n​该产品销量100万台。"
    result = extract_coverage_passages(content)

    assert result
    block_text = result[0].text
    assert "<div>" not in block_text
    assert "​" not in block_text
    assert "2025年营收增长18%" in block_text


def test_coverage_features_date_not_counted_as_number():
    features = _coverage_features("2025年3月15日发布。")
    assert features["date"] == 1.0
    assert features["number"] == 0.0


def test_coverage_features_english_entity_adjacent_to_cjk():
    assert _count_entities("OpenAI发布新模型。") == 1
    assert _count_entities("DeepSeek推出最新版模型。") == 1
    # 单个大写字母不是专有名词候选。
    assert _count_entities("发布了X产品。") == 0


def test_coverage_features_chinese_org_suffix():
    assert _count_entities("重庆大学发布了研究成果。") == 1
    assert _count_entities("该课题由国家自然科学基金委员会资助。") == 1


def test_coverage_score_weights_and_caps():
    # 数字封顶 5：10 个数字只贡献 5 分。
    assert _coverage_score({"number": 10, "date": 0, "time": 0, "entity": 0, "citation": 0}) == 5.0
    # 日期权重 2.0，普通数字权重 1.0。
    assert _coverage_score({"number": 1, "date": 0, "time": 0, "entity": 0, "citation": 0}) == 1.0
    assert _coverage_score({"number": 0, "date": 1, "time": 0, "entity": 0, "citation": 0}) == 2.0
    # 时间/实体权重 1.5，引用权重 1.0。
    assert _coverage_score({"number": 0, "date": 0, "time": 1, "entity": 0, "citation": 0}) == 1.5
    assert _coverage_score({"number": 0, "date": 0, "time": 0, "entity": 1, "citation": 0}) == 1.5
    assert _coverage_score({"number": 0, "date": 0, "time": 0, "entity": 0, "citation": 1}) == 1.0


def test_extract_neighbor_expansion_merges_adjacent_fact_paragraphs():
    content = "\n\n".join(
        [
            "OpenAI于2025年发布模型X，推理成本下降40%。",
            "该模型主要面向企业用户，目前已覆盖1000家公司。",
            "其中包括多家财富500强企业，贡献了30%的收入。",
            "以上是本文档的正文内容。",
        ]
    )
    result = extract_coverage_passages(content)

    assert len(result) == 1
    block = result[0]
    assert block.source_indices == [0, 1, 2]
    assert "OpenAI于2025年发布模型X" in block.text
    assert "目前已覆盖1000家公司" in block.text
    assert "财富500强企业" in block.text


def test_extract_separated_fact_islands_do_not_merge():
    content = "\n\n".join(
        [
            "2025年公司营收达到100亿元，同比增长20%。",
            "这段是纯背景叙述，不包含数字、日期、实体或引用，用于制造间隔。",
            "公司与华为签署战略合作，联合研发成本降低15%。",
            "公司年内共申请专利80项。",
        ]
    )
    result = extract_coverage_passages(content)

    assert len(result) == 2
    assert result[0].source_indices[0] < result[1].source_indices[0]


def test_extract_max_passages_limits_blocks():
    content = "\n\n".join(
        [
            "2025年营收100亿元，同比增长20%。",
            _FILLER,
            "2024年营收80亿元，同比增长15%。",
            _FILLER,
            "2023年营收70亿元，同比增长12%。",
            _FILLER,
            "2022年营收60亿元，同比增长10%。",
        ]
    )
    assert len(extract_coverage_passages(content, max_passages=1)) == 1
    assert len(extract_coverage_passages(content, max_passages=2)) == 2


def test_extract_near_duplicate_paragraphs_deduped():
    content = "\n\n".join(
        [
            "OpenAI于2025年发布产品X，推理性能提升50%。",
            _FILLER,
            "OpenAI于2025年发布产品X，推理性能提升50%，成为行业标杆。",
        ]
    )
    result = extract_coverage_passages(content)

    assert len(result) == 1


def test_extract_max_chars_truncates_to_budget():
    content = (
        "2025年全年营收达到1200亿元，同比增长22%，净利润率提升至15%。"
        "公司还发布了新的产品线，覆盖13个国家和地区，员工总数超过5万人。"
        "管理层预计2026年海外收入占比将突破40%。"
    )
    result = extract_coverage_passages(content, max_chars=40)

    assert result
    total_chars = sum(len(item.text) for item in result)
    assert total_chars <= 40


def test_extract_max_chars_keeps_highest_score_block_first_when_budget_tight():
    content = "\n\n".join(
        [
            "2025年公司实现营收100亿元，同比增长20%，净利润率提升至15%，海外收入占比突破30%。",
            _FILLER,
            "该产品定价4999元，覆盖30个国家和地区，已售出10万件。",
        ]
    )
    result = extract_coverage_passages(content, max_chars=60)

    # 预算只够一个块时，应完整保留分数更高者，丢弃放不下的低分块。
    assert len(result) == 1
    assert "海外收入占比突破30%" in result[0].text
    assert "该产品定价4999元" not in result[0].text
    assert sum(len(item.text) for item in result) <= 60


def test_extract_returns_blocks_in_reading_order():
    content = "\n\n".join(
        [
            "2025年营收100亿元，同比增长20%。",
            _FILLER,
            "2024年营收80亿元，同比增长15%。",
        ]
    )
    result = extract_coverage_passages(content, max_passages=2, neighbor_window=0)

    assert [item.source_indices[0] for item in result] == [0, 2]


def test_time_discourse_markers_alone_not_evidence():
    # "目前/当前/近期"是话语连接词，不是事实时间锚点，纯叙述不应进入覆盖证据。
    content = "目前该产品运行稳定，当前版本表现良好，近期暂无更新计划。"
    assert extract_coverage_passages(content) == []


def test_cjk_compound_org_not_double_counted():
    # 完整法定名/机构全称中紧邻的通用尾词不是独立实体。
    assert _count_entities("中国银行股份有限公司发布。") == 1
    assert _count_entities("清华大学附属医院。") == 1
    # 真正独立的两个机构仍应分别计数（用顿号分隔，避免连接词被并入前缀）。
    assert _count_entities("重庆大学、华中科技大学联合攻关。") == 2


def test_large_number_with_year_suffix_not_date():
    # "10000年"是数字+单位，不是日期；不应被 \d{4} 的尾部4位误判。
    features = _coverage_features("距今10000年前就有文明遗迹。")
    assert features["date"] == 0.0
    assert features["number"] == 1.0


def test_zero_budget_returns_empty():
    content = "2025年营收100亿元，同比增长20%。"
    assert extract_coverage_passages(content, max_chars=0) == []
    assert extract_coverage_passages(content, max_chars=-1) == []


def test_pipe_fact_table_row_survives_noise():
    content = "| 2024 | 100亿元 | 20% |"
    result = extract_coverage_passages(content)

    assert result
    assert "100亿元" in result[0].text


def test_pipe_nav_breadcrumb_without_digits_filtered():
    content = "首页 | 产品 | 关于我们"
    assert extract_coverage_passages(content) == []


def test_neighbor_window_expands_beyond_adjacent():
    content = "\n\n".join(
        [
            "2025年发布第一代产品，出货量10万台。",
            "2024年发布原型机，原型成本5万元。",
            "2023年完成首轮融资3亿元，投后估值达30亿元，团队扩张至40人。",
            "2022年组建团队20人。",
            "2021年立项启动，投入5000万元。",
        ]
    )
    result = extract_coverage_passages(content, max_passages=1, neighbor_window=2)

    assert len(result) == 1
    assert result[0].source_indices == [0, 1, 2, 3, 4]


def _coverage_item(text: str) -> CoveragePassage:
    return CoveragePassage(
        text=text,
        score=1.0,
        source_indices=[0],
        features={"number": 1.0, "date": 1.0, "time": 0.0, "entity": 0.0, "citation": 0.0},
    )


def test_exclude_passages_exact_duplicate_of_key_passage():
    passage = _coverage_item("2025年营收100亿元，同比增长20%。")
    assert exclude_passages([passage], ["2025年营收100亿元，同比增长20%。"]) == []


def test_exclude_passages_high_similarity_duplicate_of_key_passage():
    passage = _coverage_item("OpenAI于2025年发布产品X，推理性能提升50%，成为行业标杆。")
    assert exclude_passages(
        [passage], ["OpenAI于2025年发布产品X，推理性能提升50%。"]
    ) == []


def test_exclude_passages_keeps_non_overlapping_passage():
    passage = _coverage_item("2025年营收100亿元，同比增长20%。")
    assert exclude_passages(
        [passage], ["该产品定价4999元，覆盖30个国家和地区。"]
    ) == [passage]


def test_exclude_passages_keeps_block_with_substantial_extra_context():
    block = _coverage_item(
        "2025年，DeepSeek发布新模型，推理速度提升50%，定价99美元/月。"
        "该模型已覆盖30个国家，客户突破3万家。"
    )
    # key passage 只是块内的一小部分，块携带了额外事实上下文 → 保留。
    assert exclude_passages(
        [block], ["DeepSeek发布新模型，推理速度提升50%"]
    ) == [block]


def test_exclude_passages_with_empty_sides_returns_input():
    passage = _coverage_item("2025年营收100亿元。")
    assert exclude_passages([passage], []) == [passage]
    assert exclude_passages([], ["任何关键片段"]) == []


def test_extract_uses_bounded_cache_per_content_and_params():
    from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
        _extract_coverage_passages_cached,
    )

    content = "2025年营收100亿元，同比增长20%。"
    _extract_coverage_passages_cached.cache_clear()
    assert _extract_coverage_passages_cached.cache_info().currsize == 0

    extract_coverage_passages(content, max_passages=1, neighbor_window=0, max_chars=2000)
    extract_coverage_passages(content, max_passages=1, neighbor_window=0, max_chars=2000)
    extract_coverage_passages(content, max_passages=2, neighbor_window=0, max_chars=2000)

    info = _extract_coverage_passages_cached.cache_info()
    # (content, 全部参数) 二元组构成缓存键：同参数只算一次，不同参数是独立条目。
    assert info.currsize == 2
    assert info.misses == 2
    assert info.hits >= 1
    _extract_coverage_passages_cached.cache_clear()


def test_extract_returns_independent_objects_not_shared_by_cache():
    from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
        _extract_coverage_passages_cached,
    )

    content = "2025年营收100亿元，同比增长20%。"
    _extract_coverage_passages_cached.cache_clear()
    try:
        first = extract_coverage_passages(content, max_passages=1, neighbor_window=0, max_chars=2000)
        # 就地修改返回对象不应污染缓存或其他调用方。
        first[0].features["number"] = 999.0
        first[0].source_indices.append(99)

        second = extract_coverage_passages(content, max_passages=1, neighbor_window=0, max_chars=2000)
        assert first[0] is not second[0]
        assert second[0].features["number"] != 999.0
        assert second[0].source_indices == [0]
    finally:
        _extract_coverage_passages_cached.cache_clear()


def test_coverage_features_time_and_citation_patterns():
    # 季度/半年归入日期特征；相对时间（去年/今年…）仍属时间特征。
    features = _coverage_features(
        "去年第三季度，公司营收[1]增长5%，参考来源：x，人均成本约1,000元。"
    )
    assert features["time"] >= 1.0
    assert features["date"] >= 1.0
    assert features["citation"] >= 1.0
    assert features["number"] >= 2.0


def test_pure_citation_list_is_filtered_as_noise():
    # 纯引用/链接列表不应因引用特征得分而被选为覆盖证据。
    content = "[1] https://a.example [2] https://b.example [3] https://c.example"
    assert extract_coverage_passages(content) == []


def test_structure_feature_gated_by_fact_features():
    from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
        _coverage_features,
    )

    # 40+ 字符纯叙述段：无任何事实特征，structure 门控为 0，不会只靠长度入选。
    narrative = "这是一段不包含任何数字日期实体引用的纯叙述性文字，通篇都在描述背景、行业情况与市场环境。"
    assert len(narrative) >= 40
    assert _coverage_features(narrative)["structure"] == 0.0

    # 40+ 字符、含事实特征的段落：structure 计入长度带。
    factual = "2025年公司实现营收100亿元，同比增长20%，海外收入占比突破40%，研发投入占比18%。"
    assert len(factual) >= 40
    assert _coverage_features(factual)["structure"] == 1.0


def test_reordered_similar_paragraphs_deduped_via_jaccard():
    # v2 方案的例句：同一事实换序表达，应被近似去重判定为重复。
    content = "\n\n".join(
        [
            "OpenAI于2025年发布产品X，推理性能提升50%。",
            _FILLER,
            "2025年，OpenAI推出了产品X，推理性能提升50%。",
        ]
    )
    result = extract_coverage_passages(content, max_passages=5, neighbor_window=1)

    assert len(result) == 1


def test_max_merge_span_caps_block_size():
    # 7 段连续事实段落：跨度上限 5 → 拆成两个证据块，避免雪崩吞并。
    content = "\n\n".join(
        "2025年发布第{}代产品，出货量{}万台。".format(index + 1, 10 * (index + 1))
        for index in range(7)
    )
    result = extract_coverage_passages(
        content, max_passages=5, neighbor_window=1,
        options=CoverageOptions(max_merge_span=5),
    )

    assert result
    for block in result:
        assert len(block.source_indices) <= 5
    assert len(result) >= 2


def test_coverage_score_density_mode_normalizes_by_length():
    features = {"number": 1.0, "date": 0.0, "time": 0.0, "entity": 0.0, "citation": 0.0}
    score_short = _coverage_score(features, paragraph_len=40, score_mode="density")
    score_long = _coverage_score(features, paragraph_len=200, score_mode="density")
    assert score_short > score_long
    assert score_long == 1.0 / 200
    # 直接调用默认仍是 absolute（权重表单测语义）。
    assert _coverage_score(features) == 1.0


def test_anchor_level_dedup_drops_same_anchors_different_wording():
    # 锚点相同、措辞不同 → 判为重复并被去重。
    content = "\n\n".join(
        [
            "2025年营收100亿元，同比增长20%。",
            _FILLER,
            "2025年营收达到100亿元人民币，同比增长20个百分点。",
        ]
    )
    result = extract_coverage_passages(content)
    assert len(result) == 1


def test_anchor_level_dedup_keeps_structural_similar_different_facts():
    # 结构相似但锚点不同（版本/年份不同）→ 保留，避免把不同事实误删。
    content = "\n\n".join(
        [
            "2025年营收100亿元，同比增长20%。",
            _FILLER,
            "2024年研发投入30亿元，重点布局海外市场。",
        ]
    )
    result = extract_coverage_passages(content)
    assert len(result) == 2


def test_expansion_density_gate_reduces_neighbor_pull_at_small_k():
    # 有限 K 下，高密度已选段不拉邻居（省预算）；阈值 0 时仍拉邻居。
    content = "\n\n".join(
        [
            "2025年营收100亿元，同比增长20%，覆盖30个国家，研发投入5亿元。",
            "该产品面向企业客户，2024年发布第二代。",
        ]
    )
    no_gate = extract_coverage_passages(
        content, max_passages=1, neighbor_window=1,
        options=CoverageOptions(expansion_density_threshold=0.0),
    )
    gated = extract_coverage_passages(
        content, max_passages=1, neighbor_window=1,
        options=CoverageOptions(expansion_density_threshold=0.01),
    )
    assert no_gate[0].source_indices == [0, 1]
    assert gated[0].source_indices == [0]