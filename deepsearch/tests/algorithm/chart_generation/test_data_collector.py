# -*- coding: UTF-8 -*-

"""
ChartDataCollector._add_section_source_info 消费端防御行为的单元测试。

锁定两个关键行为：
1. 非 dict 结果项 / 非 int 溯源索引被安全跳过，不抛异常；
2. 防御日志必须使用 %-style lazy formatting（msg 占位符与参数个数匹配），
   否则 logging 在 emit 时抛 "not all arguments converted"，告警内容本身丢失。
"""

import logging

import pytest

from openjiuwen_deepsearch.algorithm.chart_generation.data_collector import ChartDataCollector

pytestmark = pytest.mark.unit

LOGGER_NAME = "openjiuwen_deepsearch.algorithm.chart_generation.data_collector"


def _make_collector(data_sources):
    """构造最小可用的 ChartDataCollector 实例。"""
    collector = ChartDataCollector("mock_model")
    collector._all_data_source_with_index = {1: data_sources}
    return collector


def test_non_dict_result_item_skipped(caplog):
    """非 dict 结果项被替换为 {}，防御日志正常格式化输出、内容不丢失。"""
    collector = _make_collector([{"title": "s0", "url": "u0"}])
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        section_data = collector._add_section_source_info(
            ["bad-item", {"data": "NO DATA"}], 1
        )
    assert section_data == [{}, {}]
    assert "skip non-dict result item" in caplog.text
    assert "'bad-item'" in caplog.text  # %r 已成功格式化，日志内容未丢失


def test_non_int_source_index_skipped(caplog):
    """str / bool 溯源索引被跳过，合法索引正常保留。"""
    data_sources = [{"title": "s0"}, {"title": "s1"}]
    collector = _make_collector(data_sources)
    item = {"data": "x", "source_indexes": ["1", True, 0]}
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        section_data = collector._add_section_source_info([item], 1)
    assert section_data[0]["source_datas"] == [data_sources[0]]
    assert "skip non-int source_index" in caplog.text


def test_valid_item_passes_through():
    """合法 dict 与合法索引正常通过，不产生防御告警。"""
    data_sources = [{"title": "s0", "url": "u0"}]
    collector = _make_collector(data_sources)
    item = {"data": "chart data", "source_indexes": [0]}
    section_data = collector._add_section_source_info([item], 1)
    assert section_data == [{"data": "chart data", "source_datas": data_sources}]
