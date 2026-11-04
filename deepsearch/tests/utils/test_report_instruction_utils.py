import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section
from openjiuwen_deepsearch.utils.common_utils.report_instruction_utils import normalize_report_format_requirements


@pytest.mark.parametrize("value, expected", [
    ("  用表格对比两种理论  ", ["用表格对比两种理论"]),
    (["列名为作者、年份、机制", "不要使用第一人称"], ["列名为作者、年份、机制", "不要使用第一人称"]),
    ([None, {}, 123, " ", "  Keep two sections.  "], ["Keep two sections."]),
    (None, []),
    ({}, []),
    (123, []),
])
def test_format_requirements_normalize_strings(value, expected):
    assert normalize_report_format_requirements(value) == expected
    section = Section(title="Theory", description="Compare two theories", format_requirements=value)
    assert section.format_requirements == expected


def test_outline_restores_format_requirements_without_interpreting_clauses():
    instruction = "用表格比较；保留原文要求。逐项说明。"
    outline = Outline.model_validate({
        "title": "Report", "thought": "Two theories", "sections": [{
            "title": "Theory", "description": "Compare two theories",
            "format_requirements": [instruction, "  Keep two main sections.  "],
        }],
    })
    assert outline.sections[0].format_requirements == [instruction, "Keep two main sections."]
