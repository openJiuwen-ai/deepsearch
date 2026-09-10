"""专业版与独立 Brief 工作流的 Prompt 隔离测试。"""

from pathlib import Path
import re


PROFESSIONAL_PROMPTS = [
    "outliner.md", "dep_driving_outliner.md", "outliner_interaction.md", "dep_driving_outliner_interaction.md",
    "planner.md", "dep_driving_planner.md", "collector_supervisor.md", "sub_section_outline.md",
    "sub_report_sidecar.md", "report_abstract_markdown.md", "report_conclusion_markdown.md",
]


def test_professional_prompts_have_no_brief_conditions():
    """专业版 Prompt 不得再携带 Brief Jinja 条件。"""
    directory = Path("openjiuwen_deepsearch/algorithm/prompts")
    for filename in PROFESSIONAL_PROMPTS:
        content = (directory / filename).read_text(encoding="utf-8")
        assert "report_type" not in content, filename
        assert not re.search(r"\{%\s*(?:if|elif)[^%]*brief", content, flags=re.IGNORECASE), filename
