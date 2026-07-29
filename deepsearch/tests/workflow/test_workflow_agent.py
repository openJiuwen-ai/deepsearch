from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.controller.schema.dataframe import JsonDataFrame, TextDataFrame
from openjiuwen.core.controller.schema.event import EventType
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutputChunk,
)
from openjiuwen.core.session.stream.base import BaseStreamMode
from openjiuwen.core.workflow import WorkflowExecutionState, generate_workflow_key
from openjiuwen.core.runner import Runner
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.config import (
    WorkflowControllerConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.config import DefaultResponse
import openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.workflow_agent as workflow_agent_module
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.workflow_agent import (
    WorkflowAgent,
    WorkflowControllerAdapter,
    _input_event_to_dict,
    _result_to_controller_output,
)
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.workflow_controller import (
    WorkflowController,
)


def _patch_runner_resource_mgr(monkeypatch: pytest.MonkeyPatch, dummy_rm: Mock) -> None:
    """Patch Runner.resource_mgr across SDK variants.

    Some openjiuwen versions implement `Runner.resource_mgr` as a read-only `@property`,
    so tests must patch the underlying `_resource_manager` field instead.
    """
    resource_mgr_attr = getattr(type(Runner), "resource_mgr", None)
    if isinstance(resource_mgr_attr, property) and resource_mgr_attr.fset is None:
        monkeypatch.setattr(Runner, "_resource_manager", dummy_rm, raising=False)
        return

    monkeypatch.setattr(Runner, "resource_mgr", dummy_rm, raising=False)


def test_input_event_to_dict_text_dataframe():
    session = Mock()
    session.get_session_id.return_value = "sid-1"

    inputs = SimpleNamespace(
        input_data=[TextDataFrame(text="hello")],
        metadata={"foo": "bar"},
    )

    out = _input_event_to_dict(inputs, session)
    assert out["conversation_id"] == "sid-1"
    assert out["query"] == "hello"
    assert out["user_id"] is None
    assert out["foo"] == "bar"


def test_input_event_to_dict_json_dataframe():
    session = Mock()
    session.get_session_id.return_value = "sid-2"

    inputs = SimpleNamespace(
        input_data=[
            JsonDataFrame(
                data={"query": "q1", "user_id": "u1", "x": 1},
            )
        ],
        metadata={"m": "v"},
    )

    out = _input_event_to_dict(inputs, session)
    assert out["conversation_id"] == "sid-2"
    assert out["query"] == "q1"
    assert out["user_id"] == "u1"
    assert out["x"] == 1
    assert out["m"] == "v"


def test_result_to_controller_output_adds_metadata_for_scalar():
    out = _result_to_controller_output("abc", input_event_id="evt-1")
    assert out.type == EventType.TASK_COMPLETION
    assert out.input_event_id == "evt-1"
    assert len(out.data) == 1

    chunk = out.data[0]
    assert isinstance(chunk, ControllerOutputChunk)
    assert chunk.last_chunk is True
    assert chunk.payload.metadata == {"result": "abc"}


def test_result_to_controller_output_no_metadata_for_dict():
    out = _result_to_controller_output({"a": 1}, input_event_id=None)
    assert out.type == EventType.TASK_COMPLETION
    assert len(out.data) == 1
    assert out.data[0].payload.metadata is None


@pytest.mark.asyncio
async def test_workflow_controller_adapter_invoke_raises_when_not_inited():
    adapter = WorkflowControllerAdapter()
    session = Mock()
    inputs = SimpleNamespace(input_data=[], metadata={}, event_id="evt-1")

    with pytest.raises(CustomValueException) as exc_info:
        await adapter.invoke(inputs=inputs, session=session)

    assert exc_info.value.error_code == StatusCode.WORKFLOW_CONTROLLER_ADAPTER_NOT_INIT.code


@pytest.mark.asyncio
async def test_workflow_controller_adapter_invoke_delegates_to_inner_and_wraps_result():
    adapter = WorkflowControllerAdapter()
    adapter._inner = Mock()
    adapter._inner.invoke = AsyncMock(return_value="scalar-result")

    session = Mock()
    session.get_session_id.return_value = "sid-3"

    inputs = SimpleNamespace(
        input_data=[TextDataFrame(text="hello")],
        metadata={"foo": "bar"},
        event_id="evt-2",
    )

    out = await adapter.invoke(inputs=inputs, session=session)

    expected_inputs_dict = {
        "conversation_id": "sid-3",
        "query": "hello",
        "user_id": None,
        "foo": "bar",
    }
    adapter._inner.invoke.assert_awaited_once_with(expected_inputs_dict, session)

    assert out.type == EventType.TASK_COMPLETION
    assert out.input_event_id == "evt-2"
    assert out.data[0].payload.metadata == {"result": "scalar-result"}


@pytest.mark.asyncio
async def test_workflow_controller_adapter_stream_delegates_to_inner_stream():
    adapter = WorkflowControllerAdapter()
    adapter._inner = Mock()
    yielded = [SimpleNamespace(kind="custom"), SimpleNamespace(kind="output")]

    async def _inner_stream(*args, **kwargs):
        for item in yielded:
            yield item

    adapter._inner.stream = _inner_stream

    session = Mock()
    session.get_session_id.return_value = "sid-4"

    inputs = SimpleNamespace(
        input_data=[TextDataFrame(text="hello")],
        metadata={},
        event_id="evt-3",
    )

    chunks = []
    async for chunk in adapter.stream(inputs=inputs, session=session):
        chunks.append(chunk)

    assert chunks == yielded


class _DummyWorkflowCard:
    def __init__(self, *, id: str, version: str, input_params: dict | None = None):
        self.id = id
        self.version = version
        self.input_params = input_params or {}


@pytest.mark.asyncio
async def test_workflow_controller_invoke_filters_inputs_and_calls_workflow(monkeypatch):
    controller = WorkflowController()

    schema = {
        "properties": {
            "query": {"type": "string"},
            "x": {"type": "integer"},
        },
        "required": ["query"],
    }
    workflow_card = _DummyWorkflowCard(id="wf", version="1", input_params=schema)
    controller.agent_config = WorkflowControllerConfig(
        id="agent-1",
        version="1.0",
        description="desc",
        workflows=[workflow_card],
    )

    workflow = Mock()
    workflow.invoke = AsyncMock(return_value=SimpleNamespace(state=None, result="final"))

    dummy_rm = Mock()
    dummy_rm.get_workflow = AsyncMock(return_value=workflow)
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    session = Mock()
    session.create_workflow_session.return_value = "wf-session"

    inputs = {
        "query": "hello",
        "conversation_id": "c1",
        "user_id": "u1",
        "x": 123,
        "y": 999,  # not in schema -> should be filtered out
    }

    res = await controller.invoke(inputs=inputs, session=session)
    assert res == "final"

    expected_workflow_key = generate_workflow_key("wf", "1")
    dummy_rm.get_workflow.assert_awaited_once()
    called_kwargs = dummy_rm.get_workflow.await_args.kwargs
    assert called_kwargs["workflow_id"] == expected_workflow_key
    assert called_kwargs["tag"] == "agent-1"

    workflow.invoke.assert_awaited_once()
    invoke_args = workflow.invoke.await_args
    assert invoke_args.args[0] == {"query": "hello", "x": 123}
    assert invoke_args.kwargs["session"] == "wf-session"


@pytest.mark.asyncio
async def test_workflow_controller_invoke_fallback_to_unkeyed_workflow(monkeypatch):
    controller = WorkflowController()
    workflow_card = _DummyWorkflowCard(id="wf", version="1", input_params={"properties": {"query": {}}})
    controller.agent_config = WorkflowControllerConfig(
        id="agent-2",
        workflows=[workflow_card],
    )

    workflow = Mock()
    workflow.invoke = AsyncMock(return_value=SimpleNamespace(state=WorkflowExecutionState.INPUT_REQUIRED, result="final-2"))

    dummy_rm = Mock()
    dummy_rm.get_workflow = AsyncMock(side_effect=[None, workflow])
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    session = Mock()
    session.create_workflow_session.return_value = "wf-session"

    res = await controller.invoke(inputs={"query": "q"}, session=session)
    assert res == "final-2"

    assert dummy_rm.get_workflow.await_count == 2
    first_call = dummy_rm.get_workflow.await_args_list[0].kwargs
    second_call = dummy_rm.get_workflow.await_args_list[1].kwargs

    assert first_call["workflow_id"] == generate_workflow_key("wf", "1")
    assert second_call["workflow_id"] == "wf"


@pytest.mark.asyncio
async def test_workflow_controller_stream_uses_runner_streaming(monkeypatch):
    controller = WorkflowController()
    workflow_card = _DummyWorkflowCard(id="wf", version="1", input_params={"properties": {"query": {}}})
    controller.agent_config = WorkflowControllerConfig(
        id="agent-stream",
        workflows=[workflow_card],
    )

    workflow = Mock()
    dummy_rm = Mock()
    dummy_rm.get_workflow = AsyncMock(return_value=workflow)
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    yielded = [SimpleNamespace(kind="custom"), SimpleNamespace(kind="output")]

    async def _fake_run_workflow_streaming(**kwargs):
        assert kwargs["workflow"] is workflow
        assert kwargs["inputs"] == {"query": "hello"}
        assert kwargs["session"] == "wf-session"
        assert kwargs["stream_modes"] == [BaseStreamMode.CUSTOM]
        for item in yielded:
            yield item

    monkeypatch.setattr(Runner, "run_workflow_streaming", _fake_run_workflow_streaming, raising=False)

    session = Mock()
    session.create_workflow_session.return_value = "wf-session"

    chunks = []
    async for chunk in controller.stream(
        inputs={"query": "hello"},
        session=session,
        stream_modes=[BaseStreamMode.CUSTOM],
    ):
        chunks.append(chunk)

    assert chunks == yielded


@pytest.mark.asyncio
async def test_workflow_controller_invoke_not_found_raises(monkeypatch):
    controller = WorkflowController()
    workflow_card = _DummyWorkflowCard(id="wf", version="1", input_params={"properties": {"query": {}}})
    controller.agent_config = WorkflowControllerConfig(
        id="agent-3",
        workflows=[workflow_card],
    )

    dummy_rm = Mock()
    dummy_rm.get_workflow = AsyncMock(return_value=None)
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    session = Mock()

    with pytest.raises(CustomValueException) as exc_info:
        await controller.invoke(inputs={"query": "q"}, session=session)

    assert exc_info.value.error_code == StatusCode.WORKFLOW_NOT_FOUND_IN_RESOURCE.code


def test_workflow_controller_get_required_input_key():
    assert WorkflowController._get_required_input_key(
        {"properties": {"query": {"type": "string"}, "x": {"type": "integer"}}, "required": []}
    ) == "query"

    assert WorkflowController._get_required_input_key(
        {"properties": {"input": {"type": "string"}, "x": {"type": "integer"}}, "required": []}
    ) == "input"

    assert WorkflowController._get_required_input_key(
        {
            "properties": {"y": {"type": "string"}},
            "required": ["y", "z"],
        }
    ) == "y"

    assert WorkflowController._get_required_input_key({"required": ["x"]}) is None


def test_workflow_controller_filter_inputs_when_schema_without_properties():
    # schema 没有 properties 包裹时，_filter_workflow_inputs 会把 schema 本身当作 properties
    schema = {"query": {"type": "string"}, "x": {"type": "integer"}}
    user_data = {"query": "q", "x": 1, "y": 2}
    out = WorkflowController._filter_workflow_inputs(schema=schema, user_data=user_data)
    assert out == {"query": "q", "x": 1}


def test_workflow_controller_config_defaults():
    cfg = WorkflowControllerConfig()
    assert cfg.id == ""
    assert cfg.version == "1.0"
    assert cfg.description == ""
    assert cfg.workflows == []

    assert isinstance(cfg.default_response, DefaultResponse)
    assert cfg.default_response.type == "text"


def test_workflow_agent_add_workflows_registers_and_handles_already_exists(monkeypatch):
    # Avoid calling WorkflowAgent.__init__ (depends on openjiuwen runtime objects),
    # we test the add_workflows method in isolation by constructing via __new__.
    agent = WorkflowAgent.__new__(WorkflowAgent)

    agent.card = SimpleNamespace(id="agent-card-id")
    agent._config = WorkflowControllerConfig(id="tag-1", description="d", workflows=[])
    # `ControllerAgent.controller` is a read-only property in the SDK; it reads `self._controller`.
    agent._controller = WorkflowControllerAdapter()
    agent._controller.setup_from_agent = Mock()

    workflow_card = SimpleNamespace(id="wf-1", version="1.0")
    workflow_instance_1 = SimpleNamespace(card=workflow_card)
    workflow_instance_2 = SimpleNamespace(card=workflow_card)  # duplicate

    class _AddResult:
        def __init__(self, *, err: bool, msg: str = ""):
            self._err = err
            self._msg = msg

        def is_err(self):
            return self._err

        def msg(self):
            return self._msg

    dummy_rm = Mock()
    expected_key = generate_workflow_key("wf-1", "1.0")

    dummy_rm.add_workflow = Mock(side_effect=[_AddResult(err=False), _AddResult(err=True, msg="already exist")])
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    agent.add_workflows([workflow_instance_1, workflow_instance_2])

    # config.workflows should not duplicate by workflow_key
    assert len(agent._config.workflows) == 1

    # add_workflow should be called twice, second error ignored
    assert dummy_rm.add_workflow.call_count == 2

    # card passed to add_workflow must use the generated workflow_key
    first_call = dummy_rm.add_workflow.call_args_list[0].kwargs
    assert first_call["card"].id == expected_key

    agent._controller.setup_from_agent.assert_called_once_with(agent)


def test_workflow_agent_add_workflows_warns_on_existing_topology_mismatch(monkeypatch, caplog):
    agent = WorkflowAgent.__new__(WorkflowAgent)

    agent.card = SimpleNamespace(id="agent-card-id")
    agent._config = WorkflowControllerConfig(id="tag-1", description="d", workflows=[])
    agent._controller = WorkflowControllerAdapter()

    workflow_card = SimpleNamespace(id="wf-1", version="1.0")
    workflow_instance = SimpleNamespace(card=workflow_card)

    class _AddResult:
        def __init__(self, *, err: bool, msg: str = ""):
            self._err = err
            self._msg = msg

        def is_err(self):
            return self._err

        def msg(self):
            return self._msg

    dummy_rm = Mock()
    dummy_rm.add_workflow = Mock(return_value=_AddResult(err=True, msg="already exist"))
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    monkeypatch.setattr(
        workflow_agent_module,
        "_resolve_workflow_instance",
        lambda item, provider, workflow_key: object(),
    )
    monkeypatch.setattr(
        workflow_agent_module,
        "_build_workflow_signature",
        lambda workflow: {
            "node_ids": ("outline", "editor_team"),
            "outline_node": "OutlineNode",
            "outline_interaction_node": "OutlineInteractionNode",
            "editor_node_id": "editor_team",
            "editor_node": "EditorTeamNode",
        },
    )
    monkeypatch.setattr(
        workflow_agent_module,
        "_get_registered_workflow_metadata",
        lambda resource_mgr, workflow_key: {
            "tags": ("tag-1",),
            "signature": {
                "node_ids": ("outline", "dependency_editor_team"),
                "outline_node": "DependencyOutlineNode",
                "outline_interaction_node": "DependencyOutlineInteractionNode",
                "editor_node_id": "dependency_editor_team",
                "editor_node": "DependencyEditorTeamNode",
            },
        },
    )

    import logging
    with caplog.at_level(logging.WARNING):
        agent.add_workflows([workflow_instance])

    assert any("different topology" in r.message for r in caplog.records)


def test_workflow_agent_add_workflows_invalid_workflow_param_raises():
    agent = WorkflowAgent.__new__(WorkflowAgent)
    agent.card = SimpleNamespace(id="agent-card-id")
    agent._config = WorkflowControllerConfig(id="tag-1", description="d", workflows=[])
    agent._controller = WorkflowControllerAdapter()

    with pytest.raises(CustomValueException) as exc_info:
        agent.add_workflows([object()])

    assert exc_info.value.error_code == StatusCode.WORKFLOW_PARAM_INVALID.code


def test_workflow_agent_add_workflows_add_failed_raises(monkeypatch):
    agent = WorkflowAgent.__new__(WorkflowAgent)
    agent.card = SimpleNamespace(id="agent-card-id")
    agent._config = WorkflowControllerConfig(id="tag-2", description="d", workflows=[])
    agent._controller = WorkflowControllerAdapter()

    workflow_card = SimpleNamespace(id="wf-err", version="1.0")
    workflow_instance = SimpleNamespace(card=workflow_card)

    class _AddResult:
        def is_err(self):
            return True

        def msg(self):
            return "some other error"

    dummy_rm = Mock()
    dummy_rm.add_workflow = Mock(return_value=_AddResult())
    _patch_runner_resource_mgr(monkeypatch, dummy_rm)

    with pytest.raises(CustomValueException) as exc_info:
        agent.add_workflows([workflow_instance])

    assert exc_info.value.error_code == StatusCode.WORKFLOW_ADD_FAILED.code


def test_workflow_agent_add_workflows_config_type_error():
    agent = WorkflowAgent.__new__(WorkflowAgent)
    agent.card = SimpleNamespace(id="agent-card-id")
    agent._config = "not-a-workflow-config"
    agent._controller = WorkflowControllerAdapter()

    with pytest.raises(CustomValueException) as exc_info:
        agent.add_workflows([SimpleNamespace(card=SimpleNamespace(id="wf", version="1"))])

    assert exc_info.value.error_code == StatusCode.WORKFLOW_AGENT_CONFIG_TYPE_ERROR.code

