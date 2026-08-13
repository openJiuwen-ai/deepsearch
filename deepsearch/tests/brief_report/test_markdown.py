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
        "A 为 30%。",
    )
    assert result.startswith("## 1 市场格局")
    assert "### 1.1 验证份额" in result
    assert "[citation:1]" in result and "[citation:9]" not in result


def test_keeps_only_first_valid_mermaid_and_removes_invalid_caption():
    """只保留首张安全且可追溯的图，并同时删除无效图题。"""
    raw = '''## 市场格局
正文。[citation:1]

**图：份额** [citation:1]
```mermaid
flowchart LR
A[30%] --> B[结论]
```

**图：危险图** [citation:1]
```mermaid
%%{init: {'securityLevel': 'loose'}}%%
flowchart LR
click A "javascript:alert(1)"
```
'''
    result = sanitize_brief_chapter(raw, SECTION, {1}, "A 为 30%。")
    assert result.count("```mermaid") == 1
    assert "份额" in result and "危险图" not in result and "javascript:" not in result


def test_removes_mermaid_when_number_is_absent_from_body_and_evidence():
    """图中数字未在正文或证据中出现时必须删除整张图。"""
    raw = '''## 市场格局
正文。[citation:1]

**图：份额** [citation:1]
```mermaid
pie title 份额
  "A" : 42
```
'''
    result = sanitize_brief_chapter(raw, SECTION, {1}, "没有数字")
    assert "```mermaid" not in result and "**图：份额**" not in result and "正文。[citation:1]" in result
