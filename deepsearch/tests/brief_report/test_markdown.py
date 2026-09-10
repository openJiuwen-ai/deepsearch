"""Brief Markdown 确定性校验测试。"""

from openjiuwen_deepsearch.algorithm.brief_report.markdown import sanitize_brief_chapter
from openjiuwen_deepsearch.algorithm.brief_report.models import BriefSection


SECTION = BriefSection.model_validate({"id": "1", "title": "市场格局", "goal": "验证格局", "research_steps": [{"id": "1-1", "requirement": "验证份额", "evidence_type": "data"}, {"id": "1-2", "requirement": "验证厂商差异", "evidence_type": "comparison"}]})


def test_sanitize_preserves_deterministic_numbered_headings_and_removes_unknown_citation():
    """编号章节进入最终报告时必须保持与报告总标题一致的二、三级层级。"""
    result = sanitize_brief_chapter(
        "# 1 市场格局\n\n## 1.1 验证份额\nA 为 30%。[citation:1][citation:9]",
        SECTION,
        {1},
    )
    assert result.startswith("## 1 市场格局")
    assert "### 1.1 验证份额" in result
    assert "[citation:1]" in result and "[citation:9]" not in result
