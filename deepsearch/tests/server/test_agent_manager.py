from types import SimpleNamespace

import pytest

from server.deepsearch.core.manager.agent import DeepSearchAgentManager
from server.schemas.deepsearch_run import DeepSearchRequest


class _FakeAgentFactory:
    def __init__(self):
        self.created = []

    def create_agent(self, config):
        agent = SimpleNamespace(config=config, research_name="demo")
        self.created.append(agent)
        return agent


class _FakeCheckpointer:
    def __init__(self):
        self.released = []

    async def release(self, session_id):
        self.released.append(session_id)


def _build_request(conversation_id: str) -> DeepSearchRequest:
    """构造用于 AgentManager 测试的最小请求对象。

    Args:
        conversation_id: 当前测试场景使用的会话 ID。

    Returns:
        DeepSearchRequest: 最小可用请求对象。
    """
    return DeepSearchRequest(
        space_id="space-1",
        conversation_id=conversation_id,
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


def test_get_or_create_agent_reuses_agent_within_same_conversation():
    factory = _FakeAgentFactory()
    manager = DeepSearchAgentManager(agent_factory=factory)
    request = _build_request("conversation-a")

    agent_a = manager.get_or_create_agent(request, object(), agent_config={"a": 1})
    agent_b = manager.get_or_create_agent(request, object(), agent_config={"a": 1})

    assert agent_a is agent_b
    assert len(factory.created) == 1


@pytest.mark.asyncio
async def test_cleanup_session_cache_evicts_agent_cache(monkeypatch):
    """清理会话时应删除该会话对应的 agent 缓存。

    Args:
        monkeypatch: pytest 运行时打桩工具。

    Returns:
        None.
    """
    factory = _FakeAgentFactory()
    manager = DeepSearchAgentManager(agent_factory=factory)
    fake_checkpointer = _FakeCheckpointer()

    monkeypatch.setattr(
        "server.deepsearch.core.manager.agent.CheckpointerFactory.get_checkpointer",
        lambda: fake_checkpointer,
    )

    request_a = _build_request("conversation-a")
    request_b = _build_request("conversation-b")

    agent_a = manager.get_or_create_agent(request_a, object(), agent_config={"a": 1})
    agent_b = manager.get_or_create_agent(request_b, object(), agent_config={"b": 2})

    assert len(factory.created) == 2

    await manager.cleanup_session_cache(request_a.space_id, request_a.conversation_id)

    # 会话 A 清理后应重新创建，不应复用旧实例。
    recreated_agent_a = manager.get_or_create_agent(request_a, object(), agent_config={"a": 1})

    assert fake_checkpointer.released == ["conversation-a"]
    assert recreated_agent_a is not agent_a
    assert manager.get_or_create_agent(request_b, object(), agent_config={"b": 2}) is agent_b


def test_build_agent_config_preserves_agent_llm_timeouts(monkeypatch):
    """验证构建的 agent 配置会透传请求里的 agent_llm_timeouts。

    Args:
        monkeypatch: pytest 运行时打桩工具。

    Returns:
        None.
    """
    factory = _FakeAgentFactory()
    manager = DeepSearchAgentManager(agent_factory=factory)
    request = _build_request("conversation-a")
    request.agent_llm_timeouts = {"default": 300, "sub_reporter": -120}

    monkeypatch.setattr(
        manager,
        "_load_web_search_config",
        lambda space_id, web_search_config, db: {
            "search_engine_name": "mock",
            "search_api_key": bytearray(b"secret"),
            "search_url": "https://example.com/search",
            "max_web_search_results": 5,
            "extension": {},
        },
    )

    config = manager.build_agent_config(request, object())

    assert config["agent_llm_timeouts"] == {"default": 300, "sub_reporter": -120}


def test_build_agent_config_passes_webpage_enrichment_enable(monkeypatch):
    """验证 server 构建 agent_config 时透传网页正文增强开关。"""
    factory = _FakeAgentFactory()
    manager = DeepSearchAgentManager(agent_factory=factory)
    request = _build_request("conversation-a")
    request.info_collector_webpage_enrich_enable = True

    monkeypatch.setattr(
        manager,
        "_load_web_search_config",
        lambda space_id, web_search_config, db: {
            "search_engine_name": "mock",
            "search_api_key": bytearray(b"secret"),
            "search_url": "https://example.com/search",
            "max_web_search_results": 5,
            "extension": {},
        },
    )

    config = manager.build_agent_config(request, object())

    assert config["info_collector_webpage_enrich_enable"] is True


def test_build_agent_config_propagates_scholarly_switch_outside_engine_config(monkeypatch):
    factory = _FakeAgentFactory()
    manager = DeepSearchAgentManager(agent_factory=factory)
    request = _build_request("conversation-scholarly")
    request.web_search_config.scholarly_search_enabled = True

    monkeypatch.setattr(
        manager,
        "_load_web_search_config",
        lambda space_id, web_search_config, db: {
            "search_engine_name": "jina",
            "search_api_key": bytearray(b"secret"),
            "search_url": "https://example.com/search",
            "max_web_search_results": 5,
            "extension": {"scholarly_search_enabled": False},
        },
    )

    config = manager.build_agent_config(request, object())

    assert config["scholarly_search_enabled"] is True
    assert config["web_search_engine_config"]["extension"] == {
        "scholarly_search_enabled": False,
    }


def test_build_agent_config_propagates_independent_scholarly_config(monkeypatch):
    manager = DeepSearchAgentManager(agent_factory=_FakeAgentFactory())
    request = _build_request("conversation-scholarly-config")
    request.web_search_config.scholarly_search_enabled = True
    request.web_search_config.scholarly_search_config.max_full_text_results_per_query = 2
    request.web_search_config.scholarly_search_config.semantic_scholar.search_api_key = "semantic-secret"
    request.web_search_config.scholarly_search_config.semantic_scholar.max_search_results = 3

    monkeypatch.setattr(
        manager,
        "_load_web_search_config",
        lambda space_id, web_search_config, db: {
            "search_engine_name": "jina",
            "search_api_key": bytearray(b"secret"),
            "search_url": "https://example.com/search",
            "max_web_search_results": 5,
            "extension": {},
        },
    )

    config = manager.build_agent_config(request, object())

    assert config["scholarly_search_enabled"] is True
    assert config["scholarly_search_config"]["max_full_text_results_per_query"] == 2
    assert config["scholarly_search_config"]["semantic_scholar"]["search_api_key"] == bytearray(b"semantic-secret")
    assert config["scholarly_search_config"]["semantic_scholar"]["max_search_results"] == 3
    assert "scholarly_max_full_text_results" not in config["web_search_engine_config"]["extension"]


def test_build_agent_config_disables_agent_llm_timeouts_without_default(monkeypatch):
    """验证构建配置时不会提前根据 default 缺失禁用 agent LLM timeout。

    Args:
        monkeypatch: pytest 运行时打桩工具。

    Returns:
        None.
    """
    factory = _FakeAgentFactory()
    manager = DeepSearchAgentManager(agent_factory=factory)
    request = _build_request("conversation-a")
    request.agent_llm_timeouts = {"sub_reporter": 120}

    monkeypatch.setattr(
        manager,
        "_load_web_search_config",
        lambda space_id, web_search_config, db: {
            "search_engine_name": "mock",
            "search_api_key": bytearray(b"secret"),
            "search_url": "https://example.com/search",
            "max_web_search_results": 5,
            "extension": {},
        },
    )

    config = manager.build_agent_config(request, object())

    assert config["agent_llm_timeouts"] == {"sub_reporter": 120}
