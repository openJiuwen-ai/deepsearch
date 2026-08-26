"""Brief 精简大纲生成的行为测试。"""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefOutlineRequest
from openjiuwen_deepsearch.algorithm.brief_report.outline import generate_brief_outline


@pytest.mark.asyncio
async def test_generate_outline_repairs_ids_and_invalid_enums(monkeypatch):
    """缺失 ID 与未知枚举不应令可用的三章大纲重试。"""
    response = {
        "content": """{"title":"AI 市场","sections":[
        {"title":"规模","goal":"验证市场规模","research_steps":[
          {"requirement":"验证 2025 年市场规模","evidence_type":"data"},
          {"requirement":"验证增长率差异","evidence_type":"invalid"}],"output_formats":["table"]},
        {"title":"厂商","goal":"验证厂商格局","research_steps":[
          {"requirement":"验证头部厂商份额"},{"requirement":"验证厂商差异"}]},
        {"title":"风险","goal":"验证主要风险","research_steps":[
          {"requirement":"验证政策风险"},{"requirement":"验证供应风险"}]}
        ]}"""
    }
    invoke = AsyncMock(return_value=response)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.outline.ainvoke_llm_with_stats",
        invoke,
    )

    result = await generate_brief_outline(
        object(),
        BriefOutlineRequest(query="分析 AI 市场", language="zh-CN", research_intent={}),
    )

    assert [section.id for section in result.sections] == ["1", "2", "3"]
    assert result.sections[0].research_steps[1].id == "1-2"
    assert result.sections[0].research_steps[1].evidence_type.value == "general"
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_generate_outline_preserves_more_than_five_valid_sections(monkeypatch):
    """有效章节不得因 Brief 的章节数量上限而被截断。"""
    invoke = AsyncMock(return_value={
        "content": """{"title":"六章报告","sections":[
        {"title":"范围","goal":"明确分析范围","research_steps":[
          {"requirement":"验证范围定义"},{"requirement":"验证对象边界"}]},
        {"title":"现状","goal":"分析当前状态","research_steps":[
          {"requirement":"验证当前数据"},{"requirement":"验证近期变化"}]},
        {"title":"驱动","goal":"分析关键驱动","research_steps":[
          {"requirement":"验证增长驱动"},{"requirement":"验证约束因素"}]},
        {"title":"竞争","goal":"分析竞争格局","research_steps":[
          {"requirement":"验证主要参与者"},{"requirement":"验证差异化因素"}]},
        {"title":"风险","goal":"分析主要风险","research_steps":[
          {"requirement":"验证外部风险"},{"requirement":"验证执行风险"}]},
        {"title":"行动","goal":"形成行动建议","research_steps":[
          {"requirement":"验证可选行动"},{"requirement":"验证实施条件"}]}
        ]}"""
    })
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.outline.ainvoke_llm_with_stats",
        invoke,
    )

    result = await generate_brief_outline(
        object(),
        BriefOutlineRequest(query="生成六章分析", language="zh-CN", research_intent={}),
    )

    assert [section.title for section in result.sections] == [
        "范围", "现状", "驱动", "竞争", "风险", "行动",
    ]


@pytest.mark.asyncio
async def test_generate_outline_retries_only_when_no_two_valid_sections(monkeypatch):
    """不足两章才触发重试，耗尽后必须向调用者报错。"""
    invoke = AsyncMock(
        side_effect=[
            {"content": "{\"title\":\"x\",\"sections\":[]}"},
            RuntimeError("boom"),
            RuntimeError("boom"),
        ]
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.outline.ainvoke_llm_with_stats",
        invoke,
    )

    with pytest.raises(ValueError, match="brief outline generation failed"):
        await generate_brief_outline(
            object(),
            BriefOutlineRequest(query="x", language="zh-CN", research_intent={}),
        )

    assert invoke.await_count == 3


@pytest.mark.asyncio
async def test_generate_outline_accepts_temporal_scope_with_date_objects(monkeypatch):
    """带日期对象的统一研究意图也必须能渲染并调用大纲模型。"""
    invoke = AsyncMock(return_value={
        "content": """{"title":"测试","sections":[
        {"title":"范围","goal":"明确范围","research_steps":[
          {"requirement":"验证截止日期","evidence_type":"timeline"},
          {"requirement":"验证相关数据","evidence_type":"data"}]},
        {"title":"结论","goal":"形成结论","research_steps":[
          {"requirement":"比较核心指标","evidence_type":"comparison"},
          {"requirement":"识别主要风险","evidence_type":"general"}]}
        ]}"""
    })
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.outline.ainvoke_llm_with_stats",
        invoke,
    )

    result = await generate_brief_outline(
        object(),
        BriefOutlineRequest(
            query="测试时间边界",
            research_intent={
                "temporal_scope": {
                    "constraint_type": "content_date",
                    "end_date": date(2024, 12, 31),
                }
            },
        ),
    )

    assert result.title == "测试"
    assert "on or before 2024-12-31" in invoke.await_args.args[1][0]["content"]
