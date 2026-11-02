from openjiuwen_deepsearch.algorithm.chart_generation.insert_chart import InsertChartNode


def test_chart_insertion_keeps_caption_without_source_links():
    result = InsertChartNode("unused").run(
        "# Report\n\nThe trend increased.",
        {"1": [{
            "anchor_match_para": "The trend increased.",
            "chart_title": "Trend",
            "description": "Annual values",
            "chart_id": "chart_1",
            "source_datas": [{"title": "Source", "url": "https://example.com/paper"}],
        }]},
    )
    assert "The trend increased." in result
    assert "(#insertChart:chart_1)" in result
    assert "**Trend**: Annual values" in result
    assert "https://example.com/paper" not in result
