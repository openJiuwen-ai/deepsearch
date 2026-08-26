# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real openjiuwen workflow graph using BaseNode and init_router."""

import pytest

from tests.unit.conftest_helpers import run

pytestmark = pytest.mark.system

NODE_START = "start"
NODE_ECHO = "echo"
NODE_END = "end"


def _branch_targets(router) -> set[str]:
    """0.1.10-era BranchRouter stores targets on ``_branches``; later versions add ``all_targets``."""
    targets: set[str] = set()
    for branch in getattr(router, "_branches", []):
        raw = getattr(branch, "target", None)
        if isinstance(raw, str):
            targets.add(raw)
        elif raw:
            targets.update(raw)
    return targets


def test_init_router_targets(openjiuwen_pkg):
    from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter

    from openjiuwen_search_base.workflow import init_router

    router = init_router(NODE_ECHO, [NODE_END, "never"])
    assert isinstance(router, BranchRouter)
    expected = {NODE_END, "never"}
    assert _branch_targets(router) == expected
    all_targets = getattr(router, "all_targets", None)
    if all_targets is not None:
        assert set(all_targets) == expected


def test_mini_workflow_invokes_base_node(openjiuwen_pkg):
    from openjiuwen.core.context_engine.base import ModelContext
    from openjiuwen.core.graph.executable import Input, Output
    from openjiuwen.core.session.node import Session
    from openjiuwen.core.workflow import (
        Workflow,
        WorkflowCard,
        WorkflowComponent,
        WorkflowExecutionState,
        create_workflow_session,
    )
    from openjiuwen.core.workflow.components.flow.end_comp import End
    from openjiuwen.core.workflow.components.flow.start_comp import Start

    from openjiuwen_search_base.workflow import BaseNode, init_router

    assert issubclass(BaseNode, WorkflowComponent)

    class EchoNode(BaseNode):
        invoked = False

        async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
            EchoNode.invoked = True
            payload = (inputs or {}).get("payload", "") if isinstance(inputs, dict) else ""
            return {"next_node": NODE_END, "echo": payload}

    EchoNode.invoked = False

    async def _run_flow():
        flow = Workflow(card=WorkflowCard(id="base_compat", version="1", name="base_compat"))
        flow.set_start_comp(NODE_START, Start())
        flow.add_workflow_comp(NODE_ECHO, EchoNode())
        flow.set_end_comp(NODE_END, End())
        flow.add_connection(NODE_START, NODE_ECHO)
        flow.add_conditional_connection(NODE_ECHO, router=init_router(NODE_ECHO, [NODE_END, "never"]))
        session = create_workflow_session()
        return await flow.invoke({"payload": "hello"}, session, skip_inputs_validate=True)

    output = run(_run_flow())
    assert EchoNode.invoked
    assert output is not None
    assert output.state == WorkflowExecutionState.COMPLETED
