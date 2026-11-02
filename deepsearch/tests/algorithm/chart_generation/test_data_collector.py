import pytest

from openjiuwen_deepsearch.algorithm.chart_generation.data_collector import ChartDataCollector


@pytest.mark.parametrize(("items", "expected"), [
    (["invalid", 123, None], [{}, {}, {}]),
    ([{"data": "NO DATA"}, {"data": ""}, {"data": []}], [{}, {}, {}]),
    ([{"data": "Annual values"}], [{"data": "Annual values"}]),
    ([{"data": {"years": [2024, 2025], "values": [1, 2]}}],
     [{"data": {"years": [2024, 2025], "values": [1, 2]}}]),
    ([{"data": "Annual values", "source_indexes": [0]}], [{"data": "Annual values"}]),
    (RuntimeError("collection failed"), []),
])
def test_normalize_chart_data(items, expected):
    assert ChartDataCollector._normalize_section_data(items) == expected
