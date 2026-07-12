import json
import logging
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report import table_caption_utils
from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_classify_scores,
    build_compact_classify_doc_infos_text,
    format_key_passage_block,
    normalize_key_passages,
)
from openjiuwen_deepsearch.algorithm.report.report import Reporter, _get_classified_infos
from openjiuwen_deepsearch.algorithm.report.table_caption_utils import ensure_markdown_table_captions
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context, session_context


def _classified_doc(title: str, url: str, source_id: str, relevance: float) -> dict:
    return {
        "title": title,
        "url": url,
        "source_id": source_id,
        "original_content": f"{title} content",
        "key_passages": [f"{title} passage"],
        "scores": {"relevance": relevance, "answerability": 0, "authority": 0, "data_density": 0},
    }


def _report_doc(idx: int, *, url: str | None = None, content: str | None = None) -> dict:
    return {
        "title": f"doc-{idx}",
        "url": url or f"https://example.com/{idx}",
        "original_content": content or f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
        "scores": {"relevance": 9, "answerability": 9, "authority": 9, "data_density": 9},
    }


def test_normalize_key_passages_cleans_non_standard_values():
    assert normalize_key_passages(["alpha", "", None, " beta "]) == ["alpha", "beta"]
    assert normalize_key_passages("single passage") == ["single passage"]
    assert normalize_key_passages(None) == []


def test_build_classify_scores_prefers_scores_over_legacy_fields():
    doc_info = {
        "scores": {"relevance": 0.9, "authority": 0.8},
        "source_authority": "legacy authority",
        "task_relevance": "legacy relevance",
        "information_richness": "legacy richness",
        "data_density": "legacy density",
    }

    assert build_classify_scores(doc_info) == {"relevance": 0.9, "authority": 0.8}


def test_build_classify_scores_ignores_legacy_score_fields():
    doc_info = {
        "source_authority": "high",
        "task_relevance": "medium",
        "information_richness": "rich",
        "data_density": "dense",
    }

    assert build_classify_scores(doc_info) == {}


def test_build_compact_classify_doc_infos_text_excludes_full_content_and_internal_fields():
    output = build_compact_classify_doc_infos_text([
        {
            "doc_id": "web_1",
            "source_id": "web_1_p1",
            "url": "https://example.com/a",
            "title": "Example title",
            "doc_time": "2026-05",
            "publish_time": "2026-05-10",
            "original_content": "SECRET FULL CONTENT",
            "query": "hidden query",
            "content_ref": {"type": "source_store", "source_id": "web_1_p1"},
            "scores": {"relevance": 0.9, "authority": 0.8},
            "source_authority": "legacy authority",
            "key_passages": ["passage 1", "passage 2"],
        },
        {
            "url": "https://example.com/b",
            "title": "Empty evidence",
            "key_passages": [],
        },
    ])

    assert "Document 1:" in output
    assert "url: https://example.com/a" in output
    assert "title: Example title" in output
    assert "doc_time: 2026-05" in output
    assert "publish_time: 2026-05-10" in output
    assert "scores:" in output
    assert "relevance: 0.9" in output
    assert "authority: 0.8" in output
    assert "key passages:" in output
    assert "- passage 1" in output
    assert "Document 2:" in output
    assert "[]" in output
    assert "original_content" not in output
    assert "SECRET FULL CONTENT" not in output
    assert "doc_id" not in output
    assert "source_id" not in output
    assert "content_ref" not in output
    assert "hidden query" not in output
    assert "legacy authority" not in output


def test_report_package_exports_compact_doc_info_helpers():
    from openjiuwen_deepsearch.algorithm.report import (
        build_compact_classify_doc_infos_text as package_build_compact,
        compact_doc_info,
    )

    assert package_build_compact is build_compact_classify_doc_infos_text
    assert compact_doc_info.build_compact_classify_doc_infos_text is build_compact_classify_doc_infos_text


@pytest.mark.asyncio
async def test_generate_sub_section_outline_calls_llm_with_preservation_context():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": "2",
            "has_template": False,
            "report_task": (
                "Part Two should be organized by five categories: "
                "1. Program Design Flaws 2. Elite Capture 3. Targeting Errors"
            ),
            "origin_query": (
                "Part Two should be organized by five categories: "
                "1. Program Design Flaws 2. Elite Capture 3. Targeting Errors"
            ),
            "current_outline": "1. Context\n2. Part Two",
            "section_task": "2 Part Two",
            "section_description": (
                "Use Program Design Flaws, Elite Capture, and Targeting Errors as exact subsection titles."
            ),
            "sub_section_core_content": [
                {"title": "evidence", "key_passages": ["Program design evidence."]}
            ],
        }

        with patch(
            "openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke, patch.object(
            reporter,
            "_generate_sub_report_sidecar",
            new_callable=AsyncMock,
            return_value={"sidecar": None, "summary": "summary", "warning": ""},
        ):
            mock_ainvoke.return_value = {
                "content": (
                    "2 Part Two\n"
                    "2.1 Program Design Flaws\n"
                    "2.2 Elite Capture\n"
                    "2.3 Targeting Errors"
                )
            }

            result = await reporter._generate_sub_section_outline(current_inputs)

        assert result["rs_success"] is True
        mock_ainvoke.assert_awaited_once()
        _, kwargs = mock_ainvoke.call_args
        assert kwargs["agent_name"] == AgentLlmName.SUB_REPORTER_OUTLINE.value
        rendered_prompt = "\n".join(message["content"] for message in kwargs["messages"])
        assert "User-Specified Subsection Preservation" in rendered_prompt
        assert "Program Design Flaws" in rendered_prompt
        assert "Elite Capture" in rendered_prompt
        assert "Targeting Errors" in rendered_prompt
    finally:
        llm_context.reset(token)


@pytest.mark.asyncio
async def test_write_subsection_reports_calls_llm_with_output_constraint_context():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": "3",
            "section_task": "3 Program Review",
            "section_description": "Create a table and enumerate each program separately.",
            "section_format_requirements": [
                "Create a summary table with columns: Country, Program Name, Program Type, Program Description.",
                "For each program, specify who was excluded and why.",
            ],
            "origin_query": (
                "Create a summary table with columns: Country, Program Name, Program Type, "
                "Program Description. For each program, specify who was excluded and why. "
                "Do not use https://blocked.example/article."
            ),
            "report_task": "Evaluate social protection programs.",
            "current_outline": "1 Context\n2 Failure Categories\n3 Program Review",
            "sub_section_outline": "3 Program Review\n3.1 Project Summary",
            "current_subsection": "3.1 Project Summary",
            "classified_content": [
                {
                    "index": 1,
                    "doc_time": "2023",
                    "original_content": (
                        "India runs Program A as a cash transfer program. Some migrant workers were excluded "
                        "because registration required local documents."
                    ),
                    "scores": {"authority": 8, "relevance": 9, "answerability": 8, "data_density": 7},
                }
            ],
            "sub_section_references": [],
            "sub_report_background_knowledge": [],
            "report_type": "professional",
            "paragraph_style": "detailed",
            "visualization_enable": False,
        }

        with patch(
            "openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke, patch.object(
            reporter,
            "_generate_sub_report_sidecar",
            new_callable=AsyncMock,
            return_value={"sidecar": None, "summary": "summary", "warning": ""},
        ):
            mock_ainvoke.return_value = {
                "content": (
                    "# 3 Program Review\n"
                    "## 3.1 Project Summary\n"
                    "| Country | Program Name | Program Type | Program Description |\n"
                    "|---|---|---|---|\n"
                    "| India | Program A | Cash transfer | Registration required local documents [citation:1]. |"
                )
            }

            result = await reporter._write_subsection_reports(current_inputs)

        assert result["success"] is True
        mock_ainvoke.assert_awaited_once()
        _, kwargs = mock_ainvoke.call_args
        assert kwargs["agent_name"] == AgentLlmName.SUB_REPORTER.value
        rendered_prompt = "\n".join(message["content"] for message in kwargs["messages"])
        assert "Authoritative Writing Context" in rendered_prompt
        assert "User Output Constraint Preservation" in rendered_prompt
        assert "Create a summary table with columns" in rendered_prompt
        assert "Country, Program Name, Program Type, Program Description" in rendered_prompt
        assert "# Original User Query" not in rendered_prompt
        assert "# Current Top-Level Section" in rendered_prompt
        assert "# Current Chapter Outline" in rendered_prompt
        assert "# Collected Evidence" in rendered_prompt
    finally:
        llm_context.reset(token)


def test_format_key_passage_block_only_outputs_passages():
    output = format_key_passage_block(
        {
            "url": "https://example.com/a",
            "title": "Example title",
            "scores": {"relevance": 0.9},
            "source_id": "source-1",
            "doc_id": "doc-1",
            "content_ref": {"type": "source_store"},
            "original_content": "SECRET FULL CONTENT",
            "key_passages": ["passage 1", "passage 2"],
        },
        3,
    )

    assert output == "Document 3 key passages:\n- passage 1\n- passage 2"
    assert "https://example.com/a" not in output
    assert "Example title" not in output
    assert "relevance" not in output
    assert "source-1" not in output
    assert "doc-1" not in output
    assert "content_ref" not in output
    assert "SECRET FULL CONTENT" not in output


def test_select_visualization_uses_structured_scores_data_density():
    selected = Reporter._select_visualization_from_classified_content([
        {
            "title": "high density",
            "scores": {"data_density": 9},
            "data_density": "legacy low score: 1",
        },
        {
            "title": "low density",
            "scores": {"data_density": 8.9},
            "data_density": "legacy high score: 10",
        },
    ])

    assert [item["title"] for item in selected] == ["high density"]


def _centered_caption(caption_text: str) -> str:
    return f'<div style="text-align: center;">\n\n**{caption_text}**\n\n</div>'


def test_clean_internal_callback_labels_keeps_natural_callbacks():
    content = (
        "[Parent Section 1] 如第1章所述，市场宽度恶化是核心背景。"
        "[Background Knowledge from Parent Section 2] 结合第2章分析，宏观预期仍偏弱。"
        "[Background Knowledge from Section 3] 延续第3章判断，科技板块仍需分化看待。"
        "[Context from Section 4] 承接第4章策略，风险控制仍是重点。"
        "[Prior Section 5] 参考第5章情景，悲观假设需要预案。"
    )

    result = Reporter._clean_internal_callback_labels(content)

    assert "[Parent Section 1]" not in result
    assert "[Background Knowledge from Parent Section 2]" not in result
    assert "[Background Knowledge from Section 3]" not in result
    assert "[Context from Section 4]" not in result
    assert "[Prior Section 5]" not in result
    assert "如第1章所述" in result
    assert "结合第2章分析" in result
    assert "延续第3章判断" in result
    assert "承接第4章策略" in result
    assert "参考第5章情景" in result


def test_clean_internal_callback_labels_removes_internal_bracket_labels():
    content = (
        "valuation is stretched [background knowledge]. "
        "policy support remains active[citation:8背景知识]. "
        "snake case leaked [citation:background_section_2]. "
        "substring section leaked [sectional]."
    )

    result = Reporter._clean_internal_callback_labels(content)

    assert "[background knowledge]" not in result
    assert "[citation:8背景知识]" not in result
    assert "[citation:background_section_2]" not in result
    assert "[sectional]" not in result
    assert "valuation is stretched" in result
    assert "policy support remains active" in result
    assert "snake case leaked" in result
    assert "substring section leaked" in result


def test_clean_internal_callback_labels_preserves_safe_brackets_and_natural_section_text():
    content = (
        "source backed [citation:8]. "
        "checked source [checked_citation:2]. "
        "normal marker [market breadth]. "
        "from is no longer an internal keyword [citation:8 from]. "
        "plain callback remains as discussed in Section 2."
    )

    result = Reporter._clean_internal_callback_labels(content)

    assert "[citation:8]" in result
    assert "[checked_citation:2]" in result
    assert "[market breadth]" in result
    assert "[citation:8 from]" in result
    assert "as discussed in Section 2" in result


def test_ensure_markdown_table_captions_adds_contextual_caption():
    content = """# 2 整车制造格局

下表汇总了合肥主要整车企业的产能与技术路线：

| 企业 | 产能 | 技术路线 |
|---|---|---|
| 比亚迪合肥基地 | 132万辆 | 纯电/混动 |

表后分析继续展开。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 2)

    assert _centered_caption("表2-1：合肥主要整车企业的产能与技术路线") in result
    assert "表2-1汇总了合肥主要整车企业的产能与技术路线：" in result
    assert result.count("表2-1") == 2


def test_ensure_markdown_table_captions_keeps_existing_caption_and_counts_order():
    content = """# 3 核心零部件产业链

| 企业 | 产品 |
|---|---|
| 国轩高科 | 动力电池 |

核心零部件企业与产品

| 领域 | 代表企业 |
|---|---|
| 电池 | 国轩高科 |
"""

    result = ensure_markdown_table_captions(content, CHINESE, 3)

    assert result.count("表3-1：核心零部件企业与产品") == 1
    assert _centered_caption("表3-1：核心零部件企业与产品") in result
    assert "表3-1梳理了核心零部件企业与产品：" in result
    assert "表3-2梳理了核心零部件产业链（领域、代表企业）：" in result
    assert _centered_caption("表3-2：核心零部件产业链（领域、代表企业）") in result


def test_ensure_markdown_table_captions_rewrites_post_table_reference():
    content = """# 3 AI芯片

| 类型 | 特征 |
|---|---|
| 端侧SoC | 低功耗 |

端侧SoC与云端AI芯片差异

如上表所示，端侧SoC更强调能效与集成度。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 3)

    assert result.count("表3-1：端侧SoC与云端AI芯片差异") == 1
    assert "如表3-1所示，端侧SoC更强调能效与集成度。" in result
    assert "如上表所示" not in result


def test_ensure_markdown_table_captions_rewrites_previous_reference_within_five_context_lines():
    content = """# 4 产业链韧性

下表汇总了关键环节的本地配套进展：

该判断还需要结合企业落地节奏观察。

政策侧的支持力度也会影响后续兑现。

| 环节 | 进展 |
|---|---|
| 功率半导体 | 已导入头部车企 |
"""

    result = ensure_markdown_table_captions(content, CHINESE, 4)

    assert "表4-1汇总了关键环节的本地配套进展：" in result
    assert "下表汇总了关键环节的本地配套进展" not in result
    assert result.count("表4-1") == 2


def test_ensure_markdown_table_captions_rewrites_next_reference_within_five_context_lines():
    content = """# 5 市场格局

| 品类 | 份额 |
|---|---|
| 新能源乘用车 | 42% |

新能源乘用车市场份额

这一结构体现出头部品类的领先优势。

后续仍需关注渗透率变化。

如上表所示，新能源乘用车仍是核心增长来源。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 5)

    assert "如表5-1所示，新能源乘用车仍是核心增长来源。" in result
    assert "如上表所示" not in result
    assert "\n\n表5-1梳理了" not in result


def test_ensure_markdown_table_captions_inserts_intro_when_reference_missing():
    content = """# 6 产业生态

这一目标的实现，离不开当前已构建的五位一体生态体系。

| 支撑维度 | 具体内容 | 数据/案例 |
|---|---|---|
| 充换电设施 | 充电枪、换电站 | 35万个充电枪 |

综上，合肥通过系统性基础设施布局构建了产业生态圈。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 6)

    assert "表6-1梳理了产业生态（支撑维度、具体内容、数据/案例）：" in result
    assert _centered_caption("表6-1：产业生态（支撑维度、具体内容、数据/案例）") in result
    assert result.count("表6-1") == 2
    assert "综上，合肥通过系统性基础设施布局构建了产业生态圈。" in result


def test_ensure_markdown_table_captions_merges_intro_into_colon_line():
    content = """# 2 EDA软件与材料领域：自主化进程与供应链安全评估

根据权威梳理，中国在多个核心材料品类已实现进口替代：

| 材料类别 | 国内唯一性突破 | 关键应用与客户 |
|---|---|---|
| 大硅片 | 12英寸量产 | 14nm逻辑芯片 |
"""

    result = ensure_markdown_table_captions(content, CHINESE, 2)

    assert (
        "根据权威梳理，中国在多个核心材料品类已实现进口替代，"
        "表2-1梳理了EDA软件与材料领域：自主化进程与供应链安全评估"
        "（材料类别、国内唯一性突破、关键应用与客户）："
    ) in result
    assert "\n\n表2-1梳理了" not in result
    assert _centered_caption(
        "表2-1：EDA软件与材料领域：自主化进程与供应链安全评估（材料类别、国内唯一性突破、关键应用与客户）"
    ) in result


def test_ensure_markdown_table_captions_moves_caption_below_table():
    content = """# 2 产业对比

表2-1：产业指标对比

| 指标 | 数值 |
|---|---|
| 产量 | 100 |

结论延续。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 2)
    caption = _centered_caption("表2-1：产业指标对比")

    assert result.count(caption) == 1
    assert result.index("| 指标 | 数值 |") < result.index(caption)


def test_ensure_markdown_table_captions_normalizes_plain_caption_below_table():
    content = """# 4 供应链结构

| 环节 | 企业 |
|---|---|
| 电池 | 国轩高科 |

表4-1：供应链代表企业

如上表所示，电池环节已有龙头企业支撑。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 4)
    caption = _centered_caption("表4-1：供应链代表企业")

    assert result.count(caption) == 1
    assert result.splitlines().count("表4-1：供应链代表企业") == 0
    assert "如表4-1所示，电池环节已有龙头企业支撑。" in result


def test_ensure_markdown_table_captions_normalizes_unnumbered_title_below_table():
    content = """# 7 创新生态

| 平台 | 方向 |
|---|---|
| 科研院所 | 电池材料 |

表格标题：创新生态科研平台布局

如上表所示，科研平台支撑技术转化。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 7)
    caption = _centered_caption("表7-1：创新生态科研平台布局")

    assert result.count(caption) == 1
    assert "表格标题：创新生态科研平台布局" not in result
    assert "如表7-1所示，科研平台支撑技术转化。" in result


def test_ensure_markdown_table_captions_normalizes_plain_llm_title_below_table():
    content = """# 1 产业发展历程

| 时间 | 事件 |
|---|---|
| 2010年 | 产业链起步 |

2010-2021年间合肥汽车产业链发展的关键节点

如上表所示，合肥汽车产业链在关键节点上持续扩展。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 1)
    caption = _centered_caption("表1-1：2010-2021年间合肥汽车产业链发展的关键节点")

    assert result.count(caption) == 1
    assert result.splitlines().count("2010-2021年间合肥汽车产业链发展的关键节点") == 0
    assert "如表1-1所示，合肥汽车产业链在关键节点上持续扩展。" in result


def test_ensure_markdown_table_captions_accepts_comma_in_plain_title():
    content = """# 1 Market structure

| Company | Share |
|---|---|
| A | 40% |

Company, share, and growth

As shown in the table above, company A keeps a leading position.
"""

    result = ensure_markdown_table_captions(content, ENGLISH, 1)
    caption = _centered_caption("Table 1-1: Company, share, and growth")

    assert result.count(caption) == 1
    assert result.splitlines().count("Company, share, and growth") == 0
    assert "As shown in the table above" not in result
    assert "As shown in Table 1-1" in result


def test_ensure_markdown_table_captions_rewrites_english_table_below_reference():
    content = """# 2 Market structure

The table below summarizes company share:

| Company | Share |
|---|---|
| A | 40% |
"""

    result = ensure_markdown_table_captions(content, ENGLISH, 2)

    assert "Table 2-1 summarizes company share:" in result
    assert "The table below" not in result


def test_ensure_markdown_table_captions_rewrites_english_from_table_above():
    content = """# 2 Market structure

| Company | Share |
|---|---|
| A | 40% |

Company share

From the table above, company A keeps a leading position.
"""

    result = ensure_markdown_table_captions(content, ENGLISH, 2)

    assert "From Table 2-1, company A keeps a leading position." in result
    assert "from the table above" not in result.lower()


def test_ensure_markdown_table_captions_rewrites_english_weak_below_reference():
    content = """# 2 Market structure

The comparison is below:

| Company | Share |
|---|---|
| A | 40% |
"""

    result = ensure_markdown_table_captions(content, ENGLISH, 2)

    assert "The comparison is Table 2-1 below:" in result


def test_ensure_markdown_table_captions_prefers_plain_llm_title_over_intro():
    content = """# 4 配套体系

下表总结了合肥主要链主企业及其吸引的部分长三角配套企业情况：

| 链主企业 | 配套领域 |
|---|---|
| 比亚迪 | 内外饰、座椅 |

合肥链主企业与长三角配套企业关系

后续分析继续展开。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 4)

    assert _centered_caption("表4-1：合肥链主企业与长三角配套企业关系") in result
    assert _centered_caption("表4-1：合肥主要链主企业及其吸引的部分长三角配套企业情况") not in result
    assert "表4-1总结了合肥主要链主企业及其吸引的部分长三角配套企业情况：" in result


def test_ensure_markdown_table_captions_keeps_title_with_driving_effect():
    content = """# 1 产业基础

为了量化分析合肥主要整车企业及其对供应链的带动作用，下表进行了总结：

| 整车企业 | 引入/强化时间 | 核心特点 | 对供应链的主要带动作用 |
|---|---|---|---|
| 江淮汽车 | 1964年成立 | 本土老牌车企 | 培育早期零部件产业基础 |

合肥主要整车企业及其供应链带动作用
"""

    result = ensure_markdown_table_captions(content, CHINESE, 1)

    assert _centered_caption("表1-1：合肥主要整车企业及其供应链带动作用") in result
    assert "合肥主要整车企业及其供应链带动作用" not in [
        line.strip() for line in result.splitlines()
    ]
    assert "为了量化分析合肥主要整车企业及其对供应链的带动作用，表1-1进行了总结：" in result


def test_ensure_markdown_table_captions_does_not_swallow_punctuated_narrative():
    content = """# 3 产业集中度

| 企业 | 份额 |
|---|---|
| A企业 | 40% |

这一数据反映出产业集中度在提升。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 3)

    assert "这一数据反映出产业集中度在提升。" in result
    assert _centered_caption("表3-1：产业集中度（企业、份额）") in result


def test_ensure_markdown_table_captions_handles_empty_and_none_input():
    assert ensure_markdown_table_captions("", CHINESE, 1) == ""
    assert ensure_markdown_table_captions(None, CHINESE, 1) is None


def test_ensure_markdown_table_captions_normalizes_section_idx_none_and_zero():
    content = """# 编号

| A | B |
|---|---|
| 1 | 2 |
"""

    none_result = ensure_markdown_table_captions(content, CHINESE, None)
    zero_result = ensure_markdown_table_captions(content, CHINESE, 0)

    assert _centered_caption("表1：编号（A、B）") in none_result
    assert _centered_caption("表0-1：编号（A、B）") in zero_result


def test_ensure_markdown_table_captions_keeps_plain_text_without_tables():
    content = "# 1 纯文本\n\n这里没有任何表格，只是普通正文。"

    result = ensure_markdown_table_captions(content, CHINESE, 1)

    assert result == content
    assert "表1-1" not in result


def test_ensure_markdown_table_captions_ignores_tables_inside_code_fences():
    content = """# 1 示例

```python
| A | B |
|---|---|
| 1 | 2 |
```

正文继续。
"""

    result = ensure_markdown_table_captions(content, CHINESE, 1)

    assert "表1-1" not in result
    assert "| A | B |" in result


def test_ensure_markdown_table_captions_warns_on_mismatched_code_fence(caplog):
    content = """# 1 示例

```python
~~~
| A | B |
|---|---|
| 1 | 2 |
"""

    with caplog.at_level(logging.WARNING, logger=table_caption_utils.__name__):
        result = ensure_markdown_table_captions(content, CHINESE, 1)

    assert "Mismatched Markdown code fence marker" in caplog.text
    assert "表1-1" not in result


def test_ensure_markdown_table_captions_splits_adjacent_tables_without_blank_line():
    content = """# 2 指标

| 指标 | 值 |
|---|---|
| A | 1 |
| 维度 | 值 |
|---|---|
| B | 2 |
"""

    result = ensure_markdown_table_captions(content, CHINESE, 2)

    assert _centered_caption("表2-1：指标（指标、值）") in result
    assert _centered_caption("表2-2：指标（维度、值）") in result
    assert result.count('<div style="text-align: center;">') == 2


def test_ensure_markdown_table_captions_truncates_long_explicit_caption():
    long_title = "核心指标" * 30
    content = f"""# 1 长标题

| 指标 | 值 |
|---|---|
| A | 1 |

表格标题：{long_title}
"""

    result = ensure_markdown_table_captions(content, CHINESE, 1)

    assert long_title not in result
    assert result.count("表1-1：") == 1


def test_table_caption_markup_cleaning_keeps_prefix_removal_separate():
    text = "**下表总结了[核心指标](https://example.com)**[citation:1]"

    assert table_caption_utils.normalize_caption_markup(text) == "下表总结了核心指标"
    assert table_caption_utils.clean_caption_text(text) == "核心指标"


def test_table_caption_line_override_keeps_existing_on_conflict(caplog):
    overrides = {3: "first rewrite"}

    with caplog.at_level(logging.DEBUG, logger=table_caption_utils.__name__):
        table_caption_utils._set_line_override(overrides, 3, "second rewrite")

    assert overrides[3] == "first rewrite"
    assert "Skip conflicting table-reference rewrite" in caplog.text


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_sub_report(mock_llm_cls, mock_ainvoke_llm):
    mock_session = MagicMock()
    mock_session.write_custom_stream = AsyncMock()
    token = session_context.set(mock_session)

    # 设置 mock 返回值
    # mock ainvoke_llm_with_stats 返回值(定义 side_effect 函数，根据输入参数返回不同结果)
    async def mock_ainvoke_llm_with_stats(llm, messages, llm_type: str = "basic", agent_name="AI", schema=None,
                                          tools=None, need_stream_out=False):
        # 遍历 messages 里的 dict，检查 content 字段
        if any("classification" in msg.get("content", "") for msg in messages):
            user_content = next(msg.get("content", "") for msg in messages if msg.get("role") == "user")
            assert "url: fake_url" in user_content
            assert "title: XX有限公司 - 企业详情" in user_content
            assert "doc_time: 2024 8月" in user_content
            assert "publish_time: 2024 8月" in user_content
            assert "scores:" in user_content
            assert "authority: 8" in user_content
            assert "relevance: 9" in user_content
            assert "answerability: 7" in user_content
            assert "data_density: 6" in user_content
            assert "key passages:" in user_content
            assert "- fake passage" in user_content
            assert "fake original_content" not in user_content
            assert "original_content" not in user_content
            assert "doc_id" not in user_content
            assert "source_id" not in user_content
            assert "content_ref" not in user_content
            assert "query:" not in user_content
            assert "key_passages" not in user_content
            return {"content": '{\"chapter\": \"企业经营与行业分析\", \"selected_url_list\": [\"fake_url\"]}'}
        elif any("subsection outline" in msg.get("content", "") for msg in messages):
            return {"content": "3 企业经营与行业分析\n3.1 经营风险评价\n3.2 杠杆风险评估"}
        elif any("professional sub report writer" in msg.get("content", "") for msg in messages):
            user_content = next(msg.get("content", "") for msg in messages if msg.get("role") == "user")
            assert "scores:" in user_content
            assert "authority: 8" in user_content
            assert "relevance: 9" in user_content
            assert "answerability: 7" in user_content
            assert "data_density: 6" in user_content
            assert "source_authority" not in user_content
            assert "task_relevance" not in user_content
            assert "information_richness" not in user_content
            assert "fake original_content" in user_content
            return {"content": "# 3 企业经营与行业分析\n\n## 3.1 经营风险评价\nfake content 1\n\n## 3.2 杠杆风险评估\nfake content 2"}
        elif any("structured sidecar" in msg.get("content", "") for msg in messages):
            return {
                "content": (
                    '{"chapter_summary":"经营与行业摘要",'
                    '"key_findings":["经营风险可控"],"risk_points":[]}'
                )
            }
        elif any("draft a specific chapter" in msg.get("content", "") for msg in messages):
            return {"content": "# 3 企业经营与行业分析\n\n## 3.1 经营风险评价\nfake content 1\n\n## 3.2 杠杆风险评估\nfake content 2"}
        else:
            return {"content": "default response"}

    mock_ainvoke_llm.side_effect = mock_ainvoke_llm_with_stats

    reporter = Reporter("basic")
    current_inputs = dict(
        has_template=False,
        language=CHINESE,
        report_template='',
        report_style='scholarly',
        section_idx=3,
        report_task='XX有限公司尽职调查报告',
        section_task='企业经营与行业分析',
        section_iscore=True,
        section_description='fake section_description',
        doc_infos=[{
            'doc_id': 'web_1',
            'source_id': 'web_1_p123',
            'doc_time': '2024 8月',
            'publish_time': '2024 8月',
            'original_content': 'fake original_content',
            'url': 'fake_url',
            'title': 'XX有限公司 - 企业详情',
            'source': 'local',
            'scores': {'authority': 8, 'relevance': 9, 'answerability': 7, 'data_density': 6},
            'key_passages': ['fake passage'],
            'content_ref': {'type': 'source_store', 'source_id': 'web_1_p123'},
        }],
        gathered_info=[{'url': 'fake_url', 'title': 'XX有限公司 - 企业详情', 'content': 'fake content'}],
        sub_evaluation_details='',
        max_generate_retry_num=3,
        max_sub_report_evaluate_num=0
    )
    success, report, sub_report_content, classified_content = await reporter.generate_sub_report(current_inputs)

    assert success is True
    assert current_inputs["sub_section_core_content"] == ["Document 1 key passages:\n- fake passage"]
    assert current_inputs["sub_report_summary"] == "经营与行业摘要"
    assert current_inputs["sub_report_chapter_sidecar"].chapter_summary == "经营与行业摘要"


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_classify_doc_infos_returns_selected_url_list(mock_llm_cls):
    reporter = Reporter("basic")
    reporter._classify_with_llm = AsyncMock(
        return_value=(True, '{"chapter": "企业经营与行业分析", "selected_url_list": ["fake_url"]}')
    )
    current_inputs = {
        "section_idx": 3,
        "section_task": "企业经营与行业分析",
        "doc_infos": [
            {
                "title": "XX有限公司 - 企业详情",
                "url": "fake_url",
                "original_content": "fake original_content",
            }
        ],
        "classify_doc_infos_single_time_num": 60,
        "classify_doc_infos_res_top_k_num": 10,
    }

    success, classified_content = await reporter._classify_doc_infos(current_inputs)

    assert success is True
    assert classified_content == {"selected_url_list": ["fake_url"]}


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_classify_doc_infos_preserves_llm_url_order(mock_llm_cls):
    reporter = Reporter("basic")
    selected_urls = ["https://example.com/order/2", "https://example.com/order/0", "https://example.com/order/1"]
    reporter._classify_with_llm = AsyncMock(
        return_value=(
            True,
            json.dumps({"chapter": "chapter", "selected_url_list": selected_urls}),
        )
    )

    docs = [_report_doc(idx, url=url) for idx, url in enumerate(selected_urls)]

    success, classified_content = await reporter._classify_doc_infos({
        "section_idx": 3,
        "section_task": "企业经营与行业分析",
        "doc_infos": docs,
        "classify_doc_infos_single_time_num": 60,
        "classify_doc_infos_res_top_k_num": len(selected_urls),
        "classify_doc_infos_prefilter_multiplier": 5,
    })

    assert success is True
    assert classified_content == {"selected_url_list": selected_urls}


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_classify_doc_infos_prefilters_and_keeps_same_url_different_content(mock_llm_cls):
    reporter = Reporter("basic")
    seen_batch_sizes = []

    async def fake_classify(current_inputs, section_task, batch):
        seen_batch_sizes.append(len(batch))
        same_url_docs = [doc for doc in batch if doc["url"] == "https://example.com/same"]
        assert len(same_url_docs) == 2
        return True, '{"selected_url_list": ["https://example.com/same"]}'

    reporter._classify_with_llm = AsyncMock(side_effect=fake_classify)
    docs = []
    for idx in range(80):
        docs.append({
            "title": f"doc-{idx}",
            "url": f"https://example.com/{idx}",
            "original_content": f"content-{idx}",
            "plan_idx": 0,
            "step_idx": idx % 4,
            "scores": {"relevance": idx % 10, "answerability": 9, "authority": 8, "data_density": 7},
        })
    docs[0]["url"] = "https://example.com/same"
    docs[0]["original_content"] = "variant A"
    docs[0]["scores"]["relevance"] = 10
    docs[1]["url"] = "https://example.com/same"
    docs[1]["original_content"] = "variant B"
    docs[1]["scores"]["relevance"] = 10

    success, classified_content = await reporter._classify_doc_infos({
        "section_idx": 3,
        "section_task": "企业经营与行业分析",
        "doc_infos": docs,
        "classify_doc_infos_single_time_num": 60,
        "classify_doc_infos_res_top_k_num": 10,
        "classify_doc_infos_prefilter_multiplier": 5,
    })

    assert success is True
    assert classified_content == {"selected_url_list": ["https://example.com/same"]}
    assert seen_batch_sizes == [50]


def test_get_classified_infos_returns_all_distinct_content_variants_for_selected_url():
    doc_infos = [
        {
            "title": "A",
            "url": "https://example.com/same",
            "original_content": "variant A",
            "key_passages": ["passage A"],
        },
        {
            "title": "A",
            "url": "https://example.com/same",
            "original_content": "variant B",
            "key_passages": ["passage B"],
        },
        {"title": "B", "url": "https://example.com/other", "original_content": "other"},
    ]

    classified_infos, classified_doc_infos = _get_classified_infos(
        doc_infos,
        ["https://example.com/same"],
    )

    assert classified_infos["references"] == ["[A](https://example.com/same)"]
    assert classified_infos["core_content_list"] == [
        "Document 1 key passages:\n- passage A",
        "Document 2 key passages:\n- passage B",
    ]
    assert classified_doc_infos == doc_infos[:2]


def test_get_classified_infos_deduplicates_same_content_without_source_id():
    doc_infos = [
        {
            "title": "A low",
            "url": "https://example.com/same",
            "original_content": "same content",
            "key_passages": ["low passage"],
            "scores": {"relevance": 1},
        },
        {
            "title": "A high",
            "url": "https://example.com/same",
            "original_content": "same content",
            "key_passages": ["high passage"],
            "scores": {"relevance": 9},
        },
    ]

    classified_infos, classified_doc_infos = _get_classified_infos(
        doc_infos,
        ["https://example.com/same"],
    )

    assert classified_infos["core_content_list"] == ["Document 1 key passages:\n- high passage"]
    assert classified_doc_infos == [doc_infos[1]]


def test_get_classified_infos_keeps_top10_source_ids_by_score():
    doc_infos = [
        _classified_doc(f"doc-{idx}", "https://example.com/same", f"source-{idx}", idx * 0.8)
        for idx in range(12)
    ]

    classified_infos, classified_doc_infos = _get_classified_infos(
        doc_infos,
        ["https://example.com/same"],
        max_source_id_count=10,
    )

    assert len(classified_doc_infos) == 10
    assert {doc["source_id"] for doc in classified_doc_infos} == {
        f"source-{idx}" for idx in range(2, 12)
    }
    assert classified_doc_infos[0]["source_id"] == "source-11"
    assert len(classified_infos["core_content_list"]) == 10
    assert classified_infos["references"] == ["[doc\\-11](https://example.com/same)"]


def test_get_classified_infos_keeps_each_selected_url_before_filling_variants():
    doc_infos = [
        _classified_doc("A-0", "https://example.com/a", "a-0", 10),
        _classified_doc("A-1", "https://example.com/a", "a-1", 9),
        _classified_doc("B", "https://example.com/b", "b-0", 1),
    ]

    classified_infos, classified_doc_infos = _get_classified_infos(
        doc_infos,
        ["https://example.com/a", "https://example.com/b"],
        max_source_id_count=2,
    )

    assert [doc["url"] for doc in classified_doc_infos] == ["https://example.com/a", "https://example.com/b"]
    assert classified_infos["references"] == [
        "[A\\-0](https://example.com/a)",
        "[B](https://example.com/b)",
    ]


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_classify_doc_infos_fallbacks_when_prefilter_result_returns_empty_urls(mock_llm_cls):
    reporter = Reporter("basic")
    calls = []

    async def fake_classify(current_inputs, section_task, batch):
        calls.append(len(batch))
        if len(calls) == 1:
            return True, '{"selected_url_list": []}'
        return True, '{"selected_url_list": ["https://example.com/1"]}'

    reporter._classify_with_llm = AsyncMock(side_effect=fake_classify)

    success, classified_content = await reporter._classify_doc_infos({
        "section_idx": 3,
        "section_task": "企业经营与行业分析",
        "doc_infos": [
            {
                "title": "doc",
                "url": "https://example.com/1",
                "original_content": "content",
                "scores": {"relevance": 9, "answerability": 9, "authority": 9, "data_density": 9},
            }
        ],
        "classify_doc_infos_single_time_num": 60,
        "classify_doc_infos_res_top_k_num": 10,
        "classify_doc_infos_prefilter_multiplier": 5,
    })

    assert success is True
    assert classified_content == {"selected_url_list": ["https://example.com/1"]}
    assert calls == [1, 1]


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_sub_report_with_background_knowledge_only(mock_llm_cls, mock_ainvoke_llm):
    mock_session = MagicMock()
    mock_session.write_custom_stream = AsyncMock()
    token = session_context.set(mock_session)
    writer_user_messages = []

    async def mock_ainvoke_llm_with_stats(llm, messages, llm_type: str = "basic", agent_name="AI", schema=None,
                                          tools=None, need_stream_out=False):
        if any("classification" in msg.get("content", "") for msg in messages):
            raise AssertionError("classification should not run when doc_infos is empty but background exists")
        if any("subsection outline" in msg.get("content", "") for msg in messages):
            return {"content": "2 企业经营分析\n2.1 上游章节要点承接\n2.2 当前章节判断"}
        if any("professional sub report writer" in msg.get("content", "") for msg in messages):
            writer_user_messages.extend(
                msg.get("content", "") for msg in messages if msg.get("role") == "user"
            )
            return {
                "content": (
                    "# 2 企业经营分析\n\n"
                    "## 2.1 上游章节要点承接\n\n"
                    "如第1章所述，公司主营业务稳定。结合第1章分析，收入结构清晰。\n\n"
                    "## 2.2 当前章节判断\n\n"
                    "延续第2章判断，风险仍需关注。"
                )
            }
        return {"content": "background summary"}

    mock_ainvoke_llm.side_effect = mock_ainvoke_llm_with_stats

    reporter = Reporter("basic")
    current_inputs = dict(
        has_template=False,
        language=CHINESE,
        report_template='',
        report_style='scholarly',
        section_idx=2,
        report_task='XX有限公司尽职调查报告',
        section_task='企业经营分析',
        section_iscore=False,
        section_description='结合父章节摘要继续撰写',
        doc_infos=[],
        gathered_info=[],
        sub_report_background_knowledge=[
            {"section_id": "1", "content_summary": "父章节总结：公司主营业务稳定，收入结构清晰。"}
        ],
        sub_evaluation_details='',
        max_generate_retry_num=3,
        max_sub_report_evaluate_num=0
    )

    success, report, sub_report_content, classified_content = await reporter.generate_sub_report(current_inputs)

    session_context.reset(token)

    assert success is True
    assert sub_report_content
    assert classified_content == []
    assert "[Parent Section 1]" not in sub_report_content
    assert "[Background Knowledge from Parent Section 1]" not in sub_report_content
    assert "[Background Knowledge from Section 2]" not in sub_report_content
    assert "如第1章所述" in sub_report_content
    assert "结合第1章分析" in sub_report_content
    assert "延续第2章判断" in sub_report_content
    assert len(current_inputs["sub_section_core_content"]) == 1
    background_content = current_inputs["sub_section_core_content"][0]
    assert background_content["section_id"] == "1"
    assert background_content["summary"] == "父章节总结：公司主营业务稳定，收入结构清晰。"
    assert "Section 1" in background_content["allowed_callback"]
    assert len(writer_user_messages) == 1
    writer_user_message = writer_user_messages[0]
    assert "Background Knowledge is" not in writer_user_message
    assert "Background Knowledge / prior-section continuity context (not citation sources)" in writer_user_message
    assert '"section_id": "1"' in writer_user_message
    assert '"summary": "父章节总结：公司主营业务稳定，收入结构清晰。"' in writer_user_message
