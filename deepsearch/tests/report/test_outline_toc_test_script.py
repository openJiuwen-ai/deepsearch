from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section
from openjiuwen_deepsearch.algorithm.report.report import Reporter


def _render_outline_body(outline):
    blocks = []
    for section in outline.sections:
        blocks.append(f"# {section.title}\n\n{section.description}")
    return "\n\n".join(blocks)


def test_render_outline_report_uses_native_clickable_toc():
    outline = Outline(
        language="zh-CN",
        title="全球票房与流媒体竞争",
        thought="按市场变化、平台竞争和趋势判断组织大纲。",
        sections=[
            Section(
                id="1",
                title="1. 全球票房变化",
                description="梳理主要市场的票房变化与影响因素。",
                focus_dimensions=["市场规模", "区域差异"],
            ),
            Section(
                id="2",
                title="2. 流媒体平台竞争",
                description="分析平台竞争格局。\n# 这不是章节标题",
                format_requirements=["使用对比表呈现关键差异"],
            ),
        ],
    )

    body = _render_outline_body(outline)
    toc = Reporter._build_table_of_contents(body, "zh-CN")
    anchored_body = Reporter._add_chapter_anchor_ids(body)
    report = f"# {outline.title}\n\n{toc}\n\n## 大纲\n\n{anchored_body}\n"

    assert report.startswith("# 全球票房与流媒体竞争\n\n# 目录")
    assert "## 大纲" in report
    assert "[1. 全球票房变化](#chapter-1)" in report
    assert "[2. 流媒体平台竞争](#chapter-2)" in report
    assert "\n- [" not in report
    assert '# 1. 全球票房变化\n<a id="chapter-1"></a>' in report
    assert '# 2. 流媒体平台竞争\n<a id="chapter-2"></a>' in report
    assert "# 1. 全球票房变化" in report
    assert "# 2. 流媒体平台竞争" in report
    assert "# 这不是章节标题" not in [
        item["title"] for item in Reporter._extract_level_one_headings(report)
    ]
