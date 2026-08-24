from datetime import date

from openjiuwen_deepsearch.algorithm.report.date_utils import (
    parse_date_window,
    parse_content_window,
    classify_temporal,
    timeliness_score,
    parse_published_date,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import TemporalScope


def test_parse_date_window_day():
    assert parse_date_window("2024-03-15") == (date(2024, 3, 15), date(2024, 3, 15))


def test_parse_date_window_invalid():
    assert parse_date_window("2099-13-45") is None
    assert parse_date_window("not-a-date") is None
    assert parse_date_window("") is None


def test_parse_date_window_rejects_no_dash():
    assert parse_date_window("20240315") is None


def test_parse_date_window_rejects_week_date():
    assert parse_date_window("2024-W01-1") is None


def test_classify_compliant_inside():
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    assert classify_temporal((date(2019, 1, 1), date(2021, 12, 31)), scope) == "compliant"


def test_classify_violation_outside():
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    assert classify_temporal((date(2010, 1, 1), date(2015, 12, 31)), scope) == "violation"


def test_classify_partial_overlap():
    scope = TemporalScope(constraint_type="content_date", end_date=date(2024, 6, 30))
    assert classify_temporal((date(2024, 1, 1), date(2024, 12, 31)), scope) == "partial"


def test_classify_unknown_no_doc_window():
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2018, 1, 1),
        end_date=date(2023, 12, 31),
    )
    assert classify_temporal(None, scope) == "unknown"


def test_classify_single_boundary_start_only():
    scope = TemporalScope(constraint_type="content_date", start_date=date(2020, 1, 1))
    assert classify_temporal((date(2021, 1, 1), date(2022, 1, 1)), scope) == "compliant"
    assert classify_temporal((date(2010, 1, 1), date(2015, 1, 1)), scope) == "violation"


def test_classify_does_not_reject_old_year():
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(1800, 1, 1),
        end_date=date(1900, 12, 31),
    )
    assert classify_temporal((date(1850, 1, 1), date(1860, 1, 1)), scope) == "compliant"


def test_classify_does_not_reject_future_year():
    scope = TemporalScope(
        constraint_type="content_date",
        start_date=date(2025, 1, 1),
        end_date=date(2030, 12, 31),
    )
    assert classify_temporal((date(2030, 1, 1), date(2030, 6, 1)), scope) == "compliant"


def test_timeliness_score_mapping():
    assert timeliness_score("compliant") == 1.0
    assert timeliness_score("partial") == -0.3
    assert timeliness_score("violation") == -1.0
    assert timeliness_score("unknown") == 0.0


def test_parse_content_window_valid_range():
    ct = {"start": "2019-01-01", "end": "2019-12-31"}
    assert parse_content_window(ct) == (date(2019, 1, 1), date(2019, 12, 31))


def test_parse_content_window_single_day_range():
    ct = {"start": "2024-03-15", "end": "2024-03-15"}
    assert parse_content_window(ct) == (date(2024, 3, 15), date(2024, 3, 15))


def test_parse_content_window_none_input():
    assert parse_content_window(None) is None


def test_parse_content_window_missing_end():
    assert parse_content_window({"start": "2019-01-01"}) is None


def test_parse_content_window_missing_start():
    assert parse_content_window({"end": "2019-12-31"}) is None


def test_parse_content_window_invalid_dates():
    assert parse_content_window({"start": "not-a-date", "end": "2019-12-31"}) is None
    assert parse_content_window({"start": "2019-01-01", "end": "2099-13-45"}) is None


def test_parse_content_window_non_dict():
    assert parse_content_window("2019-01-01") is None
    assert parse_content_window(["2019-01-01", "2019-12-31"]) is None
    assert parse_content_window(123) is None


def test_parse_content_window_reversed_range_is_none():
    """起止倒置（start 晚于 end）视为 LLM 产出异常，返回 None，避免误判 compliant。"""
    assert parse_content_window({"start": "2024-01-01", "end": "2019-12-31"}) is None
    # 单日同天不算倒置，应正常解析。
    assert parse_content_window({"start": "2019-06-01", "end": "2019-06-01"}) == (
        date(2019, 6, 1),
        date(2019, 6, 1),
    )


def test_parse_published_date_strict_iso():
    assert parse_published_date("2023-01-15") == date(2023, 1, 15)


def test_parse_published_date_iso8601_with_time():
    assert parse_published_date("2023-01-15T12:00:00Z") == date(2023, 1, 15)


def test_parse_published_date_pubmed_style():
    assert parse_published_date("2023 Jan 15") == date(2023, 1, 15)
    assert parse_published_date("2023 Mar 7") == date(2023, 3, 7)


def test_parse_published_date_rejects_year_only_and_year_month():
    assert parse_published_date("2023") is None
    assert parse_published_date("2023 Jan") is None


def test_parse_published_date_rejects_garbage_and_empty():
    assert parse_published_date(None) is None
    assert parse_published_date("") is None
    assert parse_published_date("2023 Winter") is None
    assert parse_published_date("not a date") is None
