from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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
    from server.routers import deepsearch_run

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
