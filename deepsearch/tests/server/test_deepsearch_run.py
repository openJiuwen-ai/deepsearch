from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from server.routers import deepsearch_run
from server.schemas.deepsearch_run import DeepSearchRequest


def _build_request() -> DeepSearchRequest:
    """构造 DeepSearch 路由测试所需的最小请求对象。

    Returns:
        DeepSearchRequest: 可用于 `_prepare_stream_context` 的请求对象。
    """
    return DeepSearchRequest(
        space_id="space-1",
        conversation_id="conversation-1",
        message="hello",
        llm_config={
            "general": {
                "model_name": "mock-model",
                "model_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "secret",
            }
        },
        web_search_config={
            "web_search_config_id": 1,
            "max_web_search_results": 5,
        },
        info_collector_search_method="web",
        search_mode="research",
        execution_method="parallel",
    )


def test_web_search_config_owns_scholarly_search_switch():
    request = _build_request()

    assert request.web_search_config.scholarly_search_enabled is False

    enabled_request = DeepSearchRequest.model_validate(
        {
            **request.model_dump(exclude_none=True),
            "web_search_config": {
                **request.web_search_config.model_dump(),
                "scholarly_search_enabled": True,
            },
        }
    )

    assert enabled_request.web_search_config.scholarly_search_enabled is True


def test_web_search_config_accepts_independent_scholarly_config():
    request = DeepSearchRequest.model_validate(
        {
            **_build_request().model_dump(exclude_none=True),
            "web_search_config": {
                "web_search_config_id": 1,
                "scholarly_search_enabled": True,
                "scholarly_search_config": {
                    "max_full_text_results_per_query": 2,
                    "semantic_scholar": {
                        "search_api_key": "semantic-secret",
                        "max_search_results": 3,
                    },
                },
            },
        }
    )

    assert request.web_search_config.scholarly_search_enabled is True
    assert request.web_search_config.scholarly_search_config.max_full_text_results_per_query == 2
    assert request.web_search_config.scholarly_search_config.semantic_scholar.max_search_results == 3
    assert request.web_search_config.scholarly_search_config.semantic_scholar.search_api_key == "semantic-secret"


def test_deep_search_request_accepts_agent_llm_timeouts():
    """验证请求模型允许传入按 agent 配置的 LLM 总超时。

    Returns:
        None.
    """
    request = DeepSearchRequest(
        space_id="space-1",
        conversation_id="conversation-1",
        message="hello",
        llm_config={
            "general": {
                "model_name": "mock-model",
                "model_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "secret",
            }
        },
        web_search_config={
            "web_search_config_id": 1,
            "max_web_search_results": 5,
        },
        info_collector_search_method="web",
        search_mode="research",
        execution_method="parallel",
        agent_llm_timeouts={"default": 300, "sub_reporter": 120},
    )

    assert request.agent_llm_timeouts == {"default": 300, "sub_reporter": 120}


def test_deep_search_request_accepts_webpage_enrichment_enable():
    """验证请求模型允许开启信息收集网页正文增强。"""
    request = DeepSearchRequest(
        space_id="space-1",
        conversation_id="conversation-1",
        message="hello",
        llm_config={
            "general": {
                "model_name": "mock-model",
                "model_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "secret",
            }
        },
        web_search_config={
            "web_search_config_id": 1,
            "max_web_search_results": 5,
        },
        info_collector_search_method="web",
        search_mode="research",
        execution_method="parallel",
        info_collector_webpage_enrich_enable=True,
    )

    assert request.info_collector_webpage_enrich_enable is True


def test_deep_search_request_accepts_hybrid_execution_method():
    """DeepSearchRequest 应接受 hybrid 作为 execution_method。"""
    request = DeepSearchRequest(
        space_id="space-1",
        conversation_id="conversation-1",
        message="hello",
        llm_config={
            "general": {
                "model_name": "mock-model",
                "model_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "secret",
            }
        },
        web_search_config={
            "web_search_config_id": 1,
            "max_web_search_results": 5,
        },
        info_collector_search_method="web",
        search_mode="research",
        execution_method="hybrid",
    )

    assert request.execution_method == "hybrid"


def test_deep_search_request_rejects_invalid_conversation_id():
    with pytest.raises(ValidationError) as exc_info:
        DeepSearchRequest(
            space_id="space-1",
            conversation_id="has space",
            message="hello",
            llm_config={
                "general": {
                    "model_name": "mock-model",
                    "model_type": "openai",
                    "base_url": "https://example.com/v1",
                    "api_key": "secret",
                }
            },
            web_search_config={
                "web_search_config_id": 1,
                "max_web_search_results": 5,
            },
            info_collector_search_method="web",
            search_mode="research",
            execution_method="parallel",
        )
    assert "conversation_id" in str(exc_info.value)


def test_agent_llm_timeouts_no_longer_validate_at_request_boundary():
    """验证请求模型不再在入口层校验 agent_llm_timeouts。

    Returns:
        None.
    """
    request = DeepSearchRequest(
        space_id="space-1",
        conversation_id="conversation-1",
        message="hello",
        llm_config={
            "general": {
                "model_name": "mock-model",
                "model_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "secret",
            }
        },
        web_search_config={
            "web_search_config_id": 1,
            "max_web_search_results": 5,
        },
        info_collector_search_method="web",
        search_mode="research",
        execution_method="parallel",
        agent_llm_timeouts={"sub_reporter": -120},
    )

    assert request.agent_llm_timeouts == {"sub_reporter": -120}


def test_prepare_stream_context_builds_agent_config_once(monkeypatch):
    """验证流式上下文准备阶段只构建一次 agent 配置。

    Args:
        monkeypatch: pytest 提供的运行时打桩工具。

    Returns:
        None.
    """
    fake_agent = SimpleNamespace(research_name="demo")
    fake_config = {
        "search_mode": "research",
        "execution_method": "parallel",
    }
    build_call_count = 0

    deepsearch_run.agent_manager._agent_cache.clear()

    def _fake_build_agent_config(request: DeepSearchRequest, db):
        """记录配置构建次数并返回伪配置。

        Args:
            request: DeepSearch 请求对象。
            db: 数据库会话对象。

        Returns:
            dict: 供测试使用的最小 agent 配置。
        """
        del request, db
        nonlocal build_call_count
        build_call_count += 1
        return fake_config

    monkeypatch.setattr(deepsearch_run.agent_manager, "build_agent_config", _fake_build_agent_config)
    monkeypatch.setattr(
        deepsearch_run.agent_manager._agent_factory,
        "create_agent",
        lambda config: fake_agent,
    )

    request, agent, run_kwargs = deepsearch_run._prepare_stream_context(_build_request(), object())

    assert request.conversation_id == "conversation-1"
    assert agent is fake_agent
    assert run_kwargs["agent_config"] == fake_config
    assert build_call_count == 1


def test_deep_search_request_report_type_defaults_to_none():
    """report_type 缺省为 None；显式 brief/professional 合法；非法值 422。"""
    request = _build_request()
    assert request.report_type is None

    base_kwargs = request.model_dump(exclude_none=True)

    brief_request = DeepSearchRequest(**base_kwargs, report_type="brief")
    assert brief_request.report_type == "brief"

    professional_request = DeepSearchRequest(**base_kwargs, report_type="professional")
    assert professional_request.report_type == "professional"

    with pytest.raises(ValidationError):
        DeepSearchRequest(**base_kwargs, report_type="invalid")


@pytest.mark.asyncio
async def test_run_brief_overrides_report_type_and_calls_run():
    """run_brief 强制 report_type=brief 并复用 run() 全部逻辑。"""
    request = _build_request()
    request.report_type = "professional"  # 调用方传入值应被覆盖

    with patch.object(deepsearch_run, "run", new_callable=AsyncMock) as mock_run:
        await deepsearch_run.run_brief(request, db=Mock())

    assert request.report_type == "brief"
    mock_run.assert_awaited_once()
    assert mock_run.call_args.args[0] is request


@pytest.mark.asyncio
async def test_run_brief_allows_non_research_mode():
    """薄封装不限制 search_mode：search/react 下 report_type 仍被覆盖，由下游自然忽略。"""
    request = _build_request()
    request.search_mode = "search"

    with patch.object(deepsearch_run, "run", new_callable=AsyncMock) as mock_run:
        await deepsearch_run.run_brief(request, db=Mock())

    assert request.report_type == "brief"
    mock_run.assert_awaited_once()


def _build_valid_upgrade_metadata() -> dict:
    """构造可通过 BriefOutline/ResearchIntent 校验的合法升级 metadata。"""
    return {
        "brief_outline": {
            "title": "算力经济研究",
            "sections": [
                {
                    "id": "s1",
                    "title": "理论基础",
                    "goal": "阐述算力经济基本概念",
                    "research_steps": [
                        {"id": "s1-r1", "requirement": "梳理算力经济定义"},
                        {"id": "s1-r2", "requirement": "总结政策背景"},
                    ],
                },
                {
                    "id": "s2",
                    "title": "产业格局",
                    "goal": "分析全球算力产业格局",
                    "research_steps": [
                        {"id": "s2-r1", "requirement": "梳理主要参与者"},
                        {"id": "s2-r2", "requirement": "对比区域发展差异"},
                    ],
                },
            ],
        },
        "research_intent": {"audience_role": "analyst", "tone": "formal"},
        "language": "zh-CN",
    }


def _patch_agent_manager_for_prepare(monkeypatch):
    """打桩 agent_manager，使 _prepare_stream_context 跳过数据库与真实 Agent 创建。"""
    fake_agent = SimpleNamespace(research_name="demo")
    fake_config = {"search_mode": "research", "execution_method": "parallel"}

    deepsearch_run.agent_manager._agent_cache.clear()

    monkeypatch.setattr(
        deepsearch_run.agent_manager,
        "build_agent_config",
        lambda request, db: fake_config,
    )
    monkeypatch.setattr(
        deepsearch_run.agent_manager._agent_factory,
        "create_agent",
        lambda config: fake_agent,
    )
    return fake_agent, fake_config


def test_run_metadata_accepted_and_passed_to_run_kwargs(monkeypatch):
    """合法 metadata 进入 run_kwargs['metadata']。"""
    _patch_agent_manager_for_prepare(monkeypatch)
    metadata = _build_valid_upgrade_metadata()

    request = _build_request()
    request.metadata = metadata
    _, _, run_kwargs = deepsearch_run._prepare_stream_context(request, object())

    assert run_kwargs["metadata"] == metadata


def test_run_metadata_requires_valid_brief_outline(monkeypatch):
    """metadata.brief_outline 非法时返回 400，不进入流式启动。"""
    from fastapi import HTTPException

    _patch_agent_manager_for_prepare(monkeypatch)

    request = _build_request()
    request.metadata = {
        "brief_outline": {"title": "", "sections": []},  # title 为空、章节不足
        "research_intent": {},
    }

    with pytest.raises(HTTPException) as exc_info:
        deepsearch_run._prepare_stream_context(request, object())

    assert exc_info.value.status_code == 400
    assert "upgrade metadata is invalid" in str(exc_info.value.detail)
    assert "200030" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_run_endpoint_invalid_metadata_returns_400_not_500(monkeypatch):
    """endpoint 级：非法 metadata 的 400 必须透传，不得被 run() 兜底重包装为 500。"""
    from fastapi import HTTPException

    _patch_agent_manager_for_prepare(monkeypatch)

    request = _build_request()
    request.metadata = {
        "brief_outline": {"title": "", "sections": []},
        "research_intent": {},
    }

    with pytest.raises(HTTPException) as exc_info:
        await deepsearch_run.run(request, db=Mock())

    assert exc_info.value.status_code == 400


def test_run_brief_report_type_still_validates_metadata_structure(monkeypatch):
    """校验只看结构：brief 模式下非法 metadata 同样 400，忽略契约仅在运行期注入阶段生效。"""
    from fastapi import HTTPException

    _patch_agent_manager_for_prepare(monkeypatch)

    request = _build_request()
    request.report_type = "brief"
    request.metadata = {
        "brief_outline": {"title": "", "sections": []},
        "research_intent": {},
    }

    with pytest.raises(HTTPException) as exc_info:
        deepsearch_run._prepare_stream_context(request, object())

    assert exc_info.value.status_code == 400


def test_run_brief_report_type_passes_valid_metadata_through(monkeypatch):
    """合法 metadata + brief：不 400，research 模式下照常透传（运行期由注入器忽略）。"""
    _patch_agent_manager_for_prepare(monkeypatch)

    request = _build_request()
    request.report_type = "brief"
    request.metadata = _build_valid_upgrade_metadata()

    _, _, run_kwargs = deepsearch_run._prepare_stream_context(request, object())
    assert run_kwargs["metadata"] == request.metadata


def test_run_search_mode_does_not_pass_metadata_to_agent(monkeypatch):
    """search/react 模式的 run 签名不接受 metadata，透传会导致 TypeError。"""
    _patch_agent_manager_for_prepare(monkeypatch)

    request = _build_request()
    request.search_mode = "search"
    request.metadata = _build_valid_upgrade_metadata()

    _, _, run_kwargs = deepsearch_run._prepare_stream_context(request, object())
    assert "metadata" not in run_kwargs


def test_run_metadata_forces_parallel_execution_method(monkeypatch):
    """升级运行强制并行图：Agent 构建前 execution_method 被注入器声明覆盖。"""
    _patch_agent_manager_for_prepare(monkeypatch)

    request = _build_request()
    request.execution_method = "dependency_driving"
    request.metadata = _build_valid_upgrade_metadata()

    deepsearch_run._force_execution_method_for_metadata(request)
    assert request.execution_method == "parallel"


def test_run_metadata_keeps_execution_method_when_not_forced(monkeypatch):
    """无 metadata 或注入器未声明强制时，execution_method 不被改动。"""
    request = _build_request()
    request.execution_method = "dependency_driving"

    deepsearch_run._force_execution_method_for_metadata(request)
    assert request.execution_method == "dependency_driving"
