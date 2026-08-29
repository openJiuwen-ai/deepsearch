from datetime import date

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    _resolve_source_date_scope,
    _resolve_content_date_scope,
)


def test_resolver_new_key_wins():
    ri = ResearchIntent(
        source_date_scope=TemporalScope(
            constraint_type="source_date",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    )
    assert _resolve_source_date_scope(ri).start_date == date(2026, 1, 1)


def test_resolver_legacy_dict_fallback():
    # 升级前旧会话 state：只有 temporal_scope 旧键
    legacy_state = {
        "temporal_scope": {
            "constraint_type": "source_date",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        }
    }
    assert _resolve_source_date_scope(legacy_state).start_date == date(2026, 1, 1)
    assert _resolve_content_date_scope(legacy_state) is None


def test_resolver_legacy_wrong_type_returns_none():
    legacy = {
        "temporal_scope": {
            "constraint_type": "content_date",
            "start_date": "2020-01-01",
            "end_date": "2022-12-31",
        }
    }
    assert _resolve_source_date_scope(legacy) is None
    assert _resolve_content_date_scope(legacy).start_date == date(2020, 1, 1)


def test_resolver_accepts_date_obj_and_iso_string():
    # model_dump() python 模式产出 date 对象
    dump = {
        "temporal_scope": {
            "constraint_type": "source_date",
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 12, 31),
        }
    }
    assert _resolve_source_date_scope(dump).start_date == date(2026, 1, 1)


def test_resolver_invalid_silent_none():
    bad = {"temporal_scope": {"constraint_type": "source_date"}}  # 缺两端
    assert _resolve_source_date_scope(bad) is None


def test_resolver_dict_new_key_wins_over_legacy():
    # 双约束输入的 dict：新键 source_date_scope 与旧键 temporal_scope(content_date) 并存。
    # 新键优先；旧键仅在新键缺失且 constraint_type 匹配时回退。
    ri_dict = {
        "source_date_scope": {
            "constraint_type": "source_date",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
        "temporal_scope": {
            "constraint_type": "content_date",
            "start_date": "2020-01-01",
            "end_date": "2022-12-31",
        },
    }
    assert _resolve_source_date_scope(ri_dict).start_date == date(2026, 1, 1)  # 新键优先
    assert _resolve_content_date_scope(ri_dict).start_date == date(2020, 1, 1)  # 旧键回退


def test_resolver_instance_legacy_routes_to_new_field_source_date():
    # 生产者直接构造 ResearchIntent(temporal_scope=<TemporalScope instance>)（实例形旧键）。
    # before-validator routes instance-form legacy to the
    # matching new field by constraint_type and pops temporal_scope, so source_date_scope is
    # populated and the resolver reads it via the new-field path (the instance-legacy fallback
    # branch in the resolver is now dead code for instance-form input).
    legacy = TemporalScope(
        constraint_type="source_date",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    ri = ResearchIntent(temporal_scope=legacy)
    assert ri.source_date_scope is legacy  # before-validator routed instance legacy
    assert ri.temporal_scope is None  # popped after routing
    assert _resolve_source_date_scope(ri) is legacy
    assert _resolve_content_date_scope(ri) is None


def test_resolver_instance_legacy_routes_to_new_field_content_date():
    # 对称：实例形 content_date 旧键由 before-validator 路由到 content_date_scope 并 pop。
    legacy = TemporalScope(
        constraint_type="content_date",
        start_date=date(2020, 1, 1),
        end_date=date(2022, 12, 31),
    )
    ri = ResearchIntent(temporal_scope=legacy)
    assert ri.content_date_scope is legacy
    assert ri.temporal_scope is None
    assert _resolve_content_date_scope(ri) is legacy
    assert _resolve_source_date_scope(ri) is None


def test_resolver_instance_new_field_wins_over_legacy():
    # 实例形：新键存在时，旧键 temporal_scope 不路由（has_new 为真）但仍被 pop。
    # 故 content_date 旧键被丢弃，仅新键 source_date_scope 保留（与 dict 分支语义一致）。
    new_scope = TemporalScope(
        constraint_type="source_date",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    legacy = TemporalScope(
        constraint_type="content_date",
        start_date=date(2020, 1, 1),
        end_date=date(2022, 12, 31),
    )
    ri = ResearchIntent(source_date_scope=new_scope, temporal_scope=legacy)
    assert _resolve_source_date_scope(ri) is new_scope  # 新键优先
    assert ri.temporal_scope is None  # 旧键被 pop，不再回退
    assert _resolve_content_date_scope(ri) is None  # content 旧键已丢弃
