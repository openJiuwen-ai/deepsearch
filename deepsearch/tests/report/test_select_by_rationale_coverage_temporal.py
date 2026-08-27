from datetime import date

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    TemporalSelectionOptions,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import TemporalScope


def _passage(idx, coverage, content_time=None):
    return {
        "passage_text": f"p{idx}",
        "doc_time": "",
        "publish_time": "",
        "content_time": content_time,
        "scores": {},
        "_cov": coverage,
        "_idx": idx,
    }


def test_score_gate_only_on_raw_coverage_not_weighted():
    # coverage 0.15 + 时间不符合(时间分 -1，权重 0.2) → 加权后 0.15-0.2=-0.05
    # 但门槛只看原始 coverage(0.15>0)，段落不被丢弃
    passages = [_passage(0, 0.15, {"start": "2010-01-01", "end": "2010-12-31"})]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.15}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, keys = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=5, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert len(selected) == 1  # 没被门槛丢弃


def test_high_coverage_beats_time_compliant():
    # coverage 0.9 不符合 vs 0.85 符合：差距 0.05 < 0.2，时间让符合者反超
    passages = [
        _passage(0, 0.9, {"start": "2010-01-01", "end": "2010-12-31"}),
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, keys = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=5, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert selected[0]["_idx"] == 1  # 符合者排前


def test_large_coverage_gap_time_cannot_overturn():
    # coverage 0.9 不符合 vs 0.5 符合：差距 0.4 > 0.2，时间翻不动
    passages = [
        _passage(0, 0.9, {"start": "2010-01-01", "end": "2010-12-31"}),  # 0.9-0.2=0.7
        _passage(1, 0.5, {"start": "2019-01-01", "end": "2019-12-31"}),  # 0.5+0.2=0.7 平手，稳定排序保 0 先
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.5}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, keys = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=5, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert selected[0]["_idx"] == 0


def test_source_date_no_temporal_scope_keeps_pure_coverage():
    # source_date 场景（temporal_scope 为 None）→ 不加权，纯覆盖度
    passages = [_passage(0, 0.9, None), _passage(1, 0.5, None)]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.5}},
        "filtered_passages": passages,
    }
    selected, keys = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=5, temporal=None,
    )
    assert selected[0]["_idx"] == 0


def test_known_ratio_zero_makes_sort_pure_coverage():
    """全 unknown（content_time=None）→ known_ratio=0 → effective_weight=0 → 纯覆盖度排序。"""
    passages = [_passage(0, 0.5, None), _passage(1, 0.9, None)]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.5}, "passage_1": {"r1": 0.9}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=5, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert selected[0]["_idx"] == 1  # 高覆盖度优先，时间加权未干扰


def test_mixed_signal_logs_known_ratio_and_effective_weight(caplog):
    """一半有日期一半 unknown → known_ratio=0.5 → effective_weight=0.1，日志含两者。"""
    import logging
    passages = [
        _passage(0, 0.6, {"start": "2019-01-01", "end": "2019-12-31"}),  # compliant
        _passage(1, 0.6, None),  # unknown
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.6}, "passage_1": {"r1": 0.6}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
    )
    with caplog.at_level(logging.INFO):
        Reporter._select_by_rationale_coverage(
            passages, [{"id": "r1"}], coverage_result,
            top_k=5, temporal=TemporalSelectionOptions(scope, 0.2),
        )
    assert "known_ratio=0.500" in caplog.text
    assert "effective_weight=0.1000" in caplog.text


def test_full_signal_equals_iteration1_behavior():
    """全有日期 → known_ratio=1.0 → effective_weight=0.2 = base，等价迭代 1（回归）。"""
    passages = [
        _passage(0, 0.9, {"start": "2010-01-01", "end": "2010-12-31"}),
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=5, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert selected[0]["_idx"] == 1  # 符合者反超（0.85+0.2 > 0.9-0.2）


def test_union_restore_keeps_evicted_unknown_passage():
    # top_k=1：0.9 无日期(unknown) vs 0.85 符合 → 加权后符合者挤掉高覆盖无日期者
    # 并集补回只护 unknown：被挤掉的纯覆盖 top-1 成员回到池子，两者都在，符合者仍排前
    passages = [
        _passage(0, 0.9, None),  # unknown：没日期不罚，补回
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, keys = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert len(selected) == 2  # top_k=1 + 补回 1
    assert selected[0]["_idx"] == 1  # 加权第一仍是符合者
    assert selected[1]["_idx"] == 0  # 被挤掉的 unknown 补回


def test_union_restore_skips_violation():
    # 证实不符（violation，扣分）的段落被挤掉后不补回：扣分是它挣来的
    passages = [
        _passage(0, 0.9, {"start": "2010-01-01", "end": "2010-12-31"}),  # violation
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),  # compliant
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert [p["_idx"] for p in selected] == [1]  # violation 不补回


def test_union_restore_skips_partial():
    # 部分重叠（partial，轻扣 -0.3）同样不补回
    passages = [
        _passage(0, 0.9, {"start": "2015-01-01", "end": "2020-12-31"}),  # partial
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),  # compliant
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert [p["_idx"] for p in selected] == [1]  # partial 不补回


def test_union_restore_respects_score_gate():
    # 补回同样走原始覆盖度 score>0 门槛：0 分段落不进纯覆盖 top-k，不会被补回
    passages = [
        _passage(0, 0.9, None),  # unknown：会被补回
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),
        _passage(2, 0.0, {"start": "2019-01-01", "end": "2019-12-31"}),
    ]
    coverage_result = {
        "coverage_matrix": {
            "passage_0": {"r1": 0.9},
            "passage_1": {"r1": 0.85},
            "passage_2": {"r1": 0.0},
        },
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert [p["_idx"] for p in selected] == [1, 0]  # 0 分段落不出现


def test_union_restore_dedup_across_rationales():
    # 同一段落被多个 rationale 的覆盖 top-k 命中时，只进池一次
    passages = [
        _passage(0, 0.9, {"start": "2010-01-01", "end": "2010-12-31"}),
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),
    ]
    coverage_result = {
        "coverage_matrix": {
            "passage_0": {"r1": 0.9, "r2": 0.95},
            "passage_1": {"r1": 0.85, "r2": 0.1},
        },
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}, {"id": "r2"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert len(selected) == len({id(p) for p in selected})  # 无重复
    assert [p["_idx"] for p in selected].count(0) == 1


def test_no_temporal_no_restore():
    # 无 temporal scope → 无补回，池子严格 top_k（行为与改前一致）
    passages = [_passage(0, 0.9, None), _passage(1, 0.85, None)]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=None,
    )
    assert len(selected) == 1
    assert selected[0]["_idx"] == 0


def test_union_restore_cross_rationale_overlap_drilldown():
    # review 回归用例：跨 rationale 共享高覆盖段落时的下钻补回。
    # top_k=1，2 个 rationale；纯覆盖基线：r1 选 p3(0.58)，r2 跳过已选的 p3(0.91)
    # 下钻选 p1(0.78) → 基线池 = {p3, p1}。
    # 加权（仅 p5 合规、仅 p1 无日期，其余 violation）：r2 加权序 p3(0.74,已见跳过)
    # →p5(0.79) 入选，p1(0.78,unknown) 被挤掉；补回必须把 p1 捞回来——
    # 预算是"实际补回数"，已被入池的 p3 不消耗额度。最终池必须 ⊇ 基线 {p3, p1}。
    def _p(idx, cov1, cov2, ct):
        return _passage(idx, cov1, ct) | {"_cov2": cov2}

    vt = {"start": "2010-01-01", "end": "2010-12-31"}  # violation
    passages = [
        _p(0, 0.26, 0.51, vt), _p(1, 0.4, 0.78, None), _p(2, 0.3, 0.48, vt),
        _p(3, 0.58, 0.91, vt), _p(4, 0.5, 0.28, vt),
        _p(5, 0.0, 0.62, {"start": "2019-01-01", "end": "2019-12-31"}),  # compliant
    ]
    coverage_result = {
        "coverage_matrix": {
            f"passage_{i}": {"r1": p["_cov"], "r2": p["_cov2"]}
            for i, p in enumerate(passages)
        },
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}, {"id": "r2"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    picked = {p["_idx"] for p in selected}
    assert {3, 1} <= picked  # 基线成员一个不少
    assert 5 in picked  # 合规者被加权抬进池
    assert len(selected) == 3


def test_internal_gate_type_agnostic_source_date_scope_weights():
    # 内部门控不再读 constraint_type：use_temporal 仅由
    # (temporal_scope is not None and timeliness_weight > 0) 决定。类型判定职责
    # 上移到入口 :1438（_resolve_content_date_scope 只回 content_date 或 None），
    # 故生产路径行为不变；但直接喂 source_date scope + weight>0 给
    # _select_by_rationale_coverage 时，门控不再拦截——content_date 加权照常生效。
    # 此处 source_date scope(2018-2023) + weight 0.2：p0(2010, violation, -1)
    # 加权 0.9-0.2=0.7，p1(2019, compliant, +1) 加权 0.85+0.2=1.05 → p1 反超；
    # top_k=1 取 p1，p0 是 violation(timeliness<0) 不补回，池子仍 1 条。
    passages = [
        _passage(0, 0.9, {"start": "2010-01-01", "end": "2010-12-31"}),
        _passage(1, 0.85, {"start": "2019-01-01", "end": "2019-12-31"}),
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.9}, "passage_1": {"r1": 0.85}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="source_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert len(selected) == 1  # violation 不补回
    assert selected[0]["_idx"] == 1  # compliant 反超（门控不再按 constraint_type 拦截）


def test_floor_gate_blocks_doomed_temporal_promotion():
    # 0.10 覆盖的合规段（加权 0.10+0.2=0.30）本可在 top_k=1 时挤掉 0.25 覆盖的
    # 违规段（加权 0.05），但它过不了下游 0.15 门槛、终将被 Layer 1 杀掉——
    # 地板门直接不让它参选，违规段保住席位（避免净损失一条有效证据）。
    passages = [
        _passage(0, 0.25, {"start": "2010-01-01", "end": "2010-12-31"}),  # violation
        _passage(1, 0.10, {"start": "2019-01-01", "end": "2019-12-31"}),  # compliant 但低于门槛
    ]
    coverage_result = {
        "coverage_matrix": {"passage_0": {"r1": 0.25}, "passage_1": {"r1": 0.10}},
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert [p["_idx"] for p in selected] == [0]


def test_union_restore_gate_blocks_subfloor_baseline_member():
    # 低于门槛的 unknown 段是被时间加权挤掉的纯覆盖基线成员时，不再被补回
    # （补回了也会在下游 Layer 1 被杀）。构造要点：p0 靠 p1 的 r2 覆盖度让
    # 全池过地板门（any_above_floor），其自身最大覆盖度 0.14 < 0.15；r1 上
    # p1 加权 0.13+0.1=0.23 > p0 的 0.14，p0 成为被挤掉的纯覆盖基线 top-1。
    # 含同一地板门的基线重放不再把 p0 算作基线成员 → 不补回（门关闭的对照
    # 下结果为 [1, 0]）。
    def _p(idx, cov1, cov2, ct):
        return _passage(idx, cov1, ct) | {"_cov2": cov2}

    passages = [
        _p(0, 0.14, 0.0, None),  # unknown，最大覆盖度 0.14 < 0.15
        _p(1, 0.13, 0.20, {"start": "2019-01-01", "end": "2019-12-31"}),  # compliant
    ]
    coverage_result = {
        "coverage_matrix": {
            f"passage_{i}": {"r1": p["_cov"], "r2": p["_cov2"]}
            for i, p in enumerate(passages)
        },
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}, {"id": "r2"}], coverage_result,
        top_k=1, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    assert [p["_idx"] for p in selected] == [1]


def test_union_restore_cap_limits_total_per_rationale():
    # top_k=10 → 补回封顶 15-10=5：10 个合规段（加权 0.30+0.1176=0.4176）把
    # 7 个更高覆盖的 unknown 段（0.31~0.37）全部挤出 top-10，补回只捞覆盖度
    # 最高的 5 个，每 rationale 交付总数不超过 15，下游 top-15 截断不触发。
    compliant_ct = {"start": "2019-01-01", "end": "2019-12-31"}
    passages = (
        [_passage(i, 0.30, compliant_ct) for i in range(10)]
        + [_passage(10 + j, 0.31 + j * 0.01, None) for j in range(7)]
    )
    coverage_result = {
        "coverage_matrix": {
            f"passage_{i}": {"r1": p["_cov"]} for i, p in enumerate(passages)
        },
        "filtered_passages": passages,
    }
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    selected, _ = Reporter._select_by_rationale_coverage(
        passages, [{"id": "r1"}], coverage_result,
        top_k=10, temporal=TemporalSelectionOptions(scope, 0.2),
    )
    picked = {p["_idx"] for p in selected}
    assert len(selected) == 15
    assert set(range(10)) <= picked  # 10 个合规段全部在池
    assert {12, 13, 14, 15, 16} <= picked  # 覆盖度最高的 5 个 unknown 被补回
    assert 10 not in picked and 11 not in picked  # 最低的 2 个被封顶丢弃
