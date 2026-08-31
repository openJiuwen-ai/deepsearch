# -*- coding: UTF-8 -*-
import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    normalize_report_type,
    resolve_report_type_policy,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("professional", "professional"),
        ("brief", "brief"),
        ("PROFESSIONAL", "professional"),
        ("Brief", "brief"),
        ("deep_research", None),
        ("concise", None),
        ("精简版", None),
    ],
)
def test_normalize_report_type(raw, expected):
    assert normalize_report_type(raw) == expected


def test_resolve_brief_policy():
    p = resolve_report_type_policy("brief")
    assert p.report_type == "brief"
    assert p.paragraph_style == "concise"
    assert p.require_summary_first is True
    assert p.require_methodology_and_risk is True


def test_resolve_professional_policy():
    p = resolve_report_type_policy("professional")
    assert p.report_type == "professional"
    assert p.paragraph_style == "detailed"
    assert p.require_summary_first is False
    assert p.require_methodology_and_risk is False


def test_resolve_default_policy_when_none():
    # 用户未明示 report_type 时，默认回退为 brief（精简模式）
    p = resolve_report_type_policy(None)
    assert p.report_type == "brief"
    assert p.paragraph_style == "concise"
    assert p.require_summary_first is True
    assert p.require_methodology_and_risk is True


