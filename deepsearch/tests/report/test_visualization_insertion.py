import json
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    VisualizationInsertPlanContext,
)


def _visualization_reporter() -> Reporter:
    reporter = Reporter.__new__(Reporter)
    reporter._llm = object()
    return reporter


@pytest.mark.asyncio
async def test_insert_visualization_plan_accepts_fenced_json():
    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization_insertion.ainvoke_llm_with_stats",
        new=AsyncMock(
            return_value={
                "content": '```json\n{"insertions":[{"after_row":2,"index":1}]}\n```'
            }
        ),
    ):
        result = await _visualization_reporter()._request_visualization_insert_plan(
            VisualizationInsertPlanContext(
                messages=[
                    {
                        "role": "user",
                        "content": "report\n=== VISUALIZATION DATA ===",
                    }
                ],
                current_inputs={
                    "language": "en",
                    "section_idx": 1,
                    "max_generate_retry_num": 1,
                },
                report_lines=["# Title\n", "Body paragraph.\n"],
                invalid_rows={1},
                mermaid_map={1: 'xychart-beta\n    x-axis ["A"]\n    bar [1]'},
                original_report="# Title\nBody paragraph.\n",
            )
        )

    assert result["rs_success"] is True
    assert result["plan"] == {"insertions": [{"after_row": 2, "index": 1}]}


@pytest.mark.asyncio
async def test_insert_visualization_plan_retry_preserves_report_and_visualization_data():
    mock_ainvoke = AsyncMock(
        side_effect=[
            {"content": "{}"},
            {"content": '{"insertions":[{"after_row":2,"index":1}]}'},
        ]
    )
    messages = [
        {
            "role": "user",
            "content": (
                "[ROW:1] # Title\n"
                "[ROW:2] Body paragraph.\n\n"
                "=== VISUALIZATION DATA ===\n"
                '{"index":1,"image_title":"Chart"}\n'
                "=== END VISUALIZATION DATA ===\n"
            ),
        }
    ]

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization_insertion.ainvoke_llm_with_stats",
        new=mock_ainvoke,
    ):
        result = await _visualization_reporter()._request_visualization_insert_plan(
            VisualizationInsertPlanContext(
                messages=messages,
                current_inputs={
                    "language": "en",
                    "section_idx": 1,
                    "max_generate_retry_num": 2,
                },
                report_lines=["# Title\n", "Body paragraph.\n"],
                invalid_rows={1},
                mermaid_map={1: 'xychart-beta\n    x-axis ["A"]\n    bar [1]'},
                original_report="# Title\nBody paragraph.\n",
            )
        )

    assert result["rs_success"] is True
    second_messages = mock_ainvoke.await_args_list[1].kwargs["messages"]
    second_prompt = "\n".join(
        str(message.get("content", ""))
        for message in second_messages
        if isinstance(message, dict)
    )
    assert "[ROW:2] Body paragraph." in second_prompt
    assert "=== VISUALIZATION DATA ===" in second_prompt
    assert "Your previous output is invalid" in second_prompt


@pytest.mark.asyncio
async def test_insert_visualization_keeps_multiple_charts_from_same_source_url():
    chart_one = {
        "image_title": "Sales trend",
        "image_type": "line",
        "unit": "vehicles",
        "records": [["2022", 1], ["2023", 2], ["2024", 3]],
    }
    chart_two = {
        "image_title": "Brand comparison",
        "image_type": "bar",
        "unit": "vehicles",
        "records": [["A", 3], ["B", 2], ["C", 1]],
    }
    current_inputs = {
        "language": "en",
        "section_idx": 1,
        "max_generate_retry_num": 1,
        "sub_report_content": "# Section\n\nParagraph one.\n\nParagraph two.\n",
        "classified_content": [{"url": "https://example.com/source", "index": 7}],
        "visualization_result": [
            {
                "url": "https://example.com/source",
                "sub_section_visualization_content": json.dumps(chart_one),
                "mermaid_content": 'xychart-beta\n    x-axis ["2022", "2023", "2024"]\n    line [1, 2, 3]',
            },
            {
                "url": "https://example.com/source",
                "sub_section_visualization_content": json.dumps(chart_two),
                "mermaid_content": 'xychart-beta\n    x-axis ["A", "B", "C"]\n    bar [3, 2, 1]',
            },
        ],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization_insertion.ainvoke_llm_with_stats",
        new=AsyncMock(
            return_value={
                "content": '{"insertions":[{"after_row":3,"index":1},{"after_row":5,"index":2}]}'
            }
        ),
    ):
        result = await _visualization_reporter()._insert_visualization(current_inputs)

    assert result["rs_success"] is True
    assert result["result"].count("```mermaid") == 2
    assert "**Sales trend[citation:7]**" in result["result"]
    assert "**Brand comparison[citation:7]**" in result["result"]


@pytest.mark.asyncio
async def test_insert_visualization_renders_all_chart_citation_indices():
    chart = {
        "image_title": "Vendor revenue comparison",
        "image_type": "bar",
        "unit": "million USD",
        "records": [["A", 10], ["B", 20], ["C", 30]],
    }
    current_inputs = {
        "language": "en",
        "section_idx": 1,
        "max_generate_retry_num": 1,
        "sub_report_content": "# Section\n\nVendor comparison paragraph.\n",
        "visualization_result": [
            {
                "url": "https://source.example/vendor-revenue",
                "citation_indices": [7, "8", 7, 0, "bad", 9],
                "index": "bad",
                "sub_section_visualization_content": json.dumps(chart),
                "mermaid_content": (
                    'xychart-beta\n    x-axis ["A", "B", "C"]\n'
                    "    bar [10, 20, 30]"
                ),
            }
        ],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization_insertion.ainvoke_llm_with_stats",
        new=AsyncMock(
            return_value={"content": '{"insertions":[{"after_row":3,"index":1}]}'}
        ),
    ):
        result = await _visualization_reporter()._insert_visualization(current_inputs)

    assert result["rs_success"] is True
    assert (
        "**Vendor revenue comparison[citation:7][citation:8][citation:9]**"
        in result["result"]
    )


@pytest.mark.asyncio
async def test_insert_visualization_completes_missing_chart_indices_from_llm_plan():
    chart_one = {
        "image_title": "Revenue trend",
        "image_type": "line",
        "unit": "million USD",
        "records": [["2021", 12], ["2022", 18], ["2023", 27]],
    }
    chart_two = {
        "image_title": "User segment mix",
        "image_type": "bar",
        "unit": "million users",
        "records": [["Enterprise", 4.2], ["SMB", 7.5], ["Individual", 11.3]],
    }
    current_inputs = {
        "language": "en",
        "section_idx": 1,
        "max_generate_retry_num": 1,
        "sub_report_content": "# Section\n\nParagraph one.\n\nParagraph two.\n",
        "classified_content": [{"url": "https://example.com/source", "index": 3}],
        "visualization_result": [
            {
                "url": "https://example.com/source",
                "sub_section_visualization_content": json.dumps(chart_one),
                "mermaid_content": 'xychart-beta\n    x-axis ["2021", "2022", "2023"]\n    line [12, 18, 27]',
            },
            {
                "url": "https://example.com/source",
                "sub_section_visualization_content": json.dumps(chart_two),
                "mermaid_content": 'xychart-beta\n    x-axis ["Enterprise", "SMB", "Individual"]\n    bar [4.2, 7.5, 11.3]',
            },
        ],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization_insertion.ainvoke_llm_with_stats",
        new=AsyncMock(
            return_value={"content": '{"insertions":[{"after_row":3,"index":1}]}'}
        ),
    ):
        result = await _visualization_reporter()._insert_visualization(current_inputs)

    assert result["rs_success"] is True
    assert result["result"].count("```mermaid") == 2
    assert "line [12, 18, 27]" in result["result"]
    assert "bar [4.2, 7.5, 11.3]" in result["result"]
    assert "**Revenue trend[citation:3]**" in result["result"]
    assert "**User segment mix[citation:3]**" in result["result"]
