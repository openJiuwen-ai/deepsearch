# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent 封装。

基于 ControllerAgent 实现，并通过 WorkflowControllerAdapter委托给 DeepSearch 内部 WorkflowController。
"""
from __future__ import annotations

import copy
import inspect
import logging

from typing import Any, AsyncIterator, Dict, List, Optional, Union

from openjiuwen.core.controller.base import Controller
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.schema.dataframe import JsonDataFrame, TextDataFrame
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutput,
    ControllerOutputChunk,
    ControllerOutputPayload,
)
from openjiuwen.core.controller.schema.event import EventType, InputEvent
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.stream.base import BaseStreamMode
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.base import ControllerAgent
from openjiuwen.core.workflow import WorkflowCard, generate_workflow_key

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.config import WorkflowControllerConfig
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.workflow_controller import WorkflowController
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


def _resolve_workflow_instance(item: Any, provider: Any, workflow_key: str) -> Any:
    """Best-effort resolve a workflow instance for topology inspection."""
    if hasattr(item, "_internal") and hasattr(item, "card"):
        return item
    if provider is None or not callable(provider) or inspect.iscoroutinefunction(provider):
        return None
    try:
        return provider()
    except Exception as exc:
        logger.warning("Failed to inspect workflow topology for %s: %s", workflow_key, exc)
        return None


def _build_workflow_signature(workflow: Any) -> Optional[Dict[str, Any]]:
    """Extract a compact topology signature from a workflow instance."""
    graph = getattr(getattr(workflow, "_internal", None), "_graph", None)
    nodes = getattr(graph, "nodes", None)
    if not isinstance(nodes, dict):
        return None

    node_classes = {}
    for node_id, vertex in nodes.items():
        executable = getattr(vertex, "_executable", None)
        if executable is None:
            node_classes[node_id] = type(vertex).__name__
        else:
            node_classes[node_id] = type(executable).__name__

    editor_node_id = None
    if NodeId.EDITOR_TEAM.value in node_classes:
        editor_node_id = NodeId.EDITOR_TEAM.value
    elif NodeId.DEPENDENCY_EDITOR_TEAM.value in node_classes:
        editor_node_id = NodeId.DEPENDENCY_EDITOR_TEAM.value

    return {
        "node_ids": tuple(sorted(node_classes.keys())),
        "outline_node": node_classes.get(NodeId.OUTLINE.value),
        "outline_interaction_node": node_classes.get(NodeId.OUTLINE_INTERACTION.value),
        "editor_node_id": editor_node_id,
        "editor_node": node_classes.get(editor_node_id) if editor_node_id else None,
    }


def _format_workflow_signature(signature: Optional[Dict[str, Any]]) -> str:
    """Format workflow signature for concise logging."""
    if not signature:
        return "unavailable"
    return (
        f"outline={signature.get('outline_node')}, "
        f"outline_interaction={signature.get('outline_interaction_node')}, "
        f"editor={signature.get('editor_node_id')}:{signature.get('editor_node')}, "
        f"nodes={list(signature.get('node_ids', ())) or []}"
    )


def _get_registered_workflow_metadata(resource_mgr: Any, workflow_key: str) -> Dict[str, Any]:
    """Read existing workflow tags and topology from the resource manager."""
    metadata = {"tags": (), "signature": None}
    if resource_mgr is None:
        return metadata

    tag_mgr = getattr(resource_mgr, "_tag_mgr", None)
    if tag_mgr and hasattr(tag_mgr, "get_resources_tags"):
        try:
            resource_tags = tag_mgr.get_resources_tags(workflow_key)
            if isinstance(resource_tags, (list, tuple, set)):
                metadata["tags"] = tuple(sorted(resource_tags))
        except Exception as exc:
            logger.warning("Failed to read workflow tags for %s: %s", workflow_key, exc)

    registry = getattr(resource_mgr, "_resource_registry", None)
    if registry is None or not hasattr(registry, "workflow"):
        return metadata

    workflow_mgr = registry.workflow()
    providers = getattr(workflow_mgr, "_providers", None)
    existing_provider = providers.get(workflow_key) if providers else None
    if existing_provider is None:
        return metadata

    existing_workflow = _resolve_workflow_instance(
        item=None,
        provider=existing_provider,
        workflow_key=workflow_key,
    )
    metadata["signature"] = _build_workflow_signature(existing_workflow)
    return metadata


def _input_event_to_dict(inputs: InputEvent, session: Any) -> Dict[str, Any]:
    """Convert InputEvent to the dict format expected by DeepSearch BaseController."""
    conversation_id = session.get_session_id() if session else "default_session"
    query = ""
    user_id = None
    extras: Dict[str, Any] = {}
    if inputs.input_data:
        first = inputs.input_data[0]
        if isinstance(first, TextDataFrame):
            query = getattr(first, "text", "") or ""
        elif isinstance(first, JsonDataFrame):
            data = getattr(first, "data", {}) or {}
            query = data.get("query", data.get("user_input", ""))
            user_id = data.get("user_id")
            extras = {k: v for k, v in data.items() if k not in ("query", "user_input", "user_id")}
    if inputs.metadata:
        extras.update(inputs.metadata)
    return {
        "conversation_id": conversation_id,
        "query": query,
        "user_id": user_id,
        **extras,
    }


def _result_to_controller_output(result: Any, input_event_id: Optional[str] = None) -> ControllerOutput:
    """Wrap controller invoke result into ControllerOutput."""
    if result is None:
        result = {"output": "processed"}
    payload = ControllerOutputPayload(
        type=EventType.TASK_COMPLETION,
        data=[],
        metadata={"result": result} if not isinstance(result, (list, dict)) else None,
    )
    chunk = ControllerOutputChunk(index=0, payload=payload, last_chunk=True)
    return ControllerOutput(
        type=EventType.TASK_COMPLETION,
        data=[chunk],
        input_event_id=input_event_id,
    )


class WorkflowControllerAdapter(Controller):
    """适配 openjiuwen Controller 接口并委托本地 WorkflowController。"""

    def __init__(self):
        super().__init__()
        self._inner: Optional[WorkflowController] = None

    def init(
        self,
        card: AgentCard,
        config: ControllerConfig,
        ability_manager: Any,
        context_engine: Any,
    ):
        """初始化适配器并注入内部 WorkflowController。"""
        self._card = card
        self._config = config
        self._ability_manager = ability_manager
        self._context_engine = context_engine
        self._inner = WorkflowController()

        # Build minimal fake agent for setup_from_agent(agent)
        class _FakeAgent:
            def __init__(self, card, context_engine, agent_config):
                self.card = card
                self.context_engine = context_engine
                self.agent_config = agent_config

        agent_config = config if isinstance(config, WorkflowControllerConfig) else self._config
        if not hasattr(agent_config, "workflows"):
            agent_config = WorkflowControllerConfig(
                id=getattr(config, "id", card.id),
                version=getattr(config, "version", "1.0"),
                description=getattr(config, "description", card.name or ""),
                workflows=[],
            )
        fake = _FakeAgent(card, context_engine, agent_config)
        self._inner.setup_from_agent(fake)
        logger.info(f"WorkflowControllerAdapter initialized for card.id={card.id}")

    async def invoke(
        self,
        inputs: InputEvent,
        session: Any,
        **kwargs,
    ) -> ControllerOutput:
        """Delegate to inner WorkflowController.invoke with dict inputs."""
        if self._inner is None:
            raise CustomValueException(
                StatusCode.WORKFLOW_CONTROLLER_ADAPTER_NOT_INIT.code,
                StatusCode.WORKFLOW_CONTROLLER_ADAPTER_NOT_INIT.errmsg,
            )
        inputs_dict = _input_event_to_dict(inputs, session)
        result = await self._inner.invoke(inputs_dict, session)
        return _result_to_controller_output(result, getattr(inputs, "event_id", None))

    async def stream(
        self,
        inputs: InputEvent,
        session: Any,
        stream_modes: Optional[List[BaseStreamMode]] = None,
        **kwargs,
    ) -> AsyncIterator[Any]:
        """Delegate to inner stream and forward workflow chunks."""
        if self._inner is None:
            raise CustomValueException(
                StatusCode.WORKFLOW_CONTROLLER_ADAPTER_NOT_INIT.code,
                StatusCode.WORKFLOW_CONTROLLER_ADAPTER_NOT_INIT.errmsg,
            )
        inputs_dict = _input_event_to_dict(inputs, session)
        async for chunk in self._inner.stream(inputs_dict, session, stream_modes=stream_modes):
            yield chunk

    def setup_from_agent(self, agent: Any) -> None:
        """Delegate to inner WorkflowController.setup_from_agent."""
        if self._inner is not None:
            self._inner.setup_from_agent(agent)


class WorkflowAgent(ControllerAgent):
    """基于 Workflow 的 Agent 实现。"""

    def __init__(
        self,
        card: AgentCard,
        config: Optional[Union[WorkflowControllerConfig, Dict]] = None,
    ):
        """Initialize with AgentCard and optional workflow config.

        Args:
            card: Agent card (required by new ControllerAgent).
            config: WorkflowControllerConfig or dict. If None,
                a default WorkflowControllerConfig is used.
        """
        if config is None:
            config = WorkflowControllerConfig(
                id=card.id,
                description=card.name or card.id,
            )
        elif isinstance(config, dict):
            config = WorkflowControllerConfig(**config)
        controller = WorkflowControllerAdapter()
        super().__init__(card=card, controller=controller, config=config)

    def add_workflows(self, workflows: List[Any]) -> None:
        """Register workflow instances with Runner.resource_mgr and add cards to config.

        Supports Workflow instance (with .card) or callable provider with id/version.
        """
        if not isinstance(self._config, WorkflowControllerConfig):
            raise CustomValueException(
                StatusCode.WORKFLOW_AGENT_CONFIG_TYPE_ERROR.code,
                StatusCode.WORKFLOW_AGENT_CONFIG_TYPE_ERROR.errmsg,
            )
        config = self._config
        if not config.workflows:
            config.workflows = []

        for item in workflows:
            if callable(item) and hasattr(item, "card") and callable(getattr(item, "card", None)):
                provider = item
                workflow_card = item.card()
            elif callable(item) and hasattr(item, "id") and hasattr(item, "version"):
                provider = item
                workflow_card = WorkflowCard(
                    id=getattr(item, "id"),
                    name=getattr(item, "name", None),
                    description=getattr(item, "description", None),
                    version=getattr(item, "version"),
                    input_params=getattr(item, "input_params", None) or getattr(item, "inputs", None),
                )
            elif hasattr(item, "card"):
                def _provider(w):
                    def _inner():
                        return w

                    return _inner

                provider = _provider(item)
                workflow_card = item.card
            else:
                raise CustomValueException(
                    StatusCode.WORKFLOW_PARAM_INVALID.code,
                    StatusCode.WORKFLOW_PARAM_INVALID.errmsg,
                )

            workflow_key = generate_workflow_key(workflow_card.id, workflow_card.version)
            existing = {generate_workflow_key(w.id, w.version) for w in config.workflows}
            if workflow_key not in existing:
                config.workflows.append(workflow_card)

            card_copy = copy.deepcopy(workflow_card)
            card_copy.id = workflow_key
            tag = config.id or self.card.id or "default"
            workflow_instance = _resolve_workflow_instance(
                item=item,
                provider=provider,
                workflow_key=workflow_key,
            )
            workflow_signature = _build_workflow_signature(workflow_instance)
            logger.info(
                "Registering workflow %s for WorkflowAgent %s with tag=%s, signature=%s",
                workflow_key,
                self.card.id,
                tag,
                _format_workflow_signature(workflow_signature),
            )
            add_result = Runner.resource_mgr.add_workflow(
                card=card_copy, workflow=provider, tag=tag
            )
            if add_result.is_err():
                err = add_result.error() if hasattr(add_result, "error") else add_result.msg()
                err_str = str(err) if err else ""
                if "already exist" in err_str.lower():
                    registered_metadata = _get_registered_workflow_metadata(Runner.resource_mgr, workflow_key)
                    registered_tags = registered_metadata.get("tags", ())
                    registered_signature = registered_metadata.get("signature")
                    if registered_tags and tag not in registered_tags:
                        logger.warning(
                            "Workflow %s already exists under tags %s, requested tag=%s",
                            workflow_key,
                            registered_tags,
                            tag,
                        )
                    if (
                        workflow_signature is not None
                        and registered_signature is not None
                        and workflow_signature != registered_signature
                    ):
                        logger.warning(
                            "Workflow %s already registered with different topology under tag=%s: "
                            "existing=%s, requested=%s",
                            workflow_key,
                            tag,
                            _format_workflow_signature(registered_signature),
                            _format_workflow_signature(workflow_signature),
                        )
                    logger.info(
                        "Workflow %s already registered for tag=%s, existing_signature=%s",
                        workflow_key,
                        tag,
                        _format_workflow_signature(registered_signature),
                    )
                else:
                    raise CustomValueException(
                        StatusCode.WORKFLOW_ADD_FAILED.code,
                        StatusCode.WORKFLOW_ADD_FAILED.errmsg.format(
                            workflow_key=workflow_key, err=str(err)
                        ),
                    ) from (err if isinstance(err, Exception) else None)
            logger.info(
                "Added workflow %s to WorkflowAgent %s with signature=%s",
                workflow_key,
                self.card.id,
                _format_workflow_signature(workflow_signature),
            )

        if self.controller and isinstance(self.controller, WorkflowControllerAdapter):
            self.controller.setup_from_agent(self)
