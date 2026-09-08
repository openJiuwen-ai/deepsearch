# -*- coding: UTF-8 -*-
"""passages_extractor.md 模板渲染测试。

验证 ``extract_content_time`` 开关下，Content Time Extraction 指导块与
输出格式里的 ``content_time`` 字段是否随开关渲染/隐藏。
"""
from openjiuwen_deepsearch.algorithm.prompts.template import get_prompt_section


def _render_passages_extractor(extract_content_time: bool) -> str:
    return get_prompt_section(
        "passages_extractor",
        {"CURRENT_TIME": "2026-08-22", "extract_content_time": extract_content_time},
    )


def test_passages_extractor_renders_content_time_block_when_enabled():
    rendered = _render_passages_extractor(extract_content_time=True)

    # Content Time Extraction 指导块出现
    assert "Content Time Extraction" in rendered
    # 输出格式里的 content_time 字段出现
    assert "content_time" in rendered
    # front-matter CURRENT_TIME 被渲染（非裸模板变量）
    assert "CURRENT TIME: 2026-08-22" in rendered
    assert "{{CURRENT_TIME}}" not in rendered


def test_passages_extractor_hides_content_time_block_when_disabled():
    rendered = _render_passages_extractor(extract_content_time=False)

    # Content Time Extraction 指导块不出现
    assert "Content Time Extraction" not in rendered
    # 输出格式里的 content_time 字段不出现
    assert "content_time" not in rendered
    # CURRENT_TIME 仍渲染
    assert "CURRENT TIME: 2026-08-22" in rendered
    assert "{{CURRENT_TIME}}" not in rendered
