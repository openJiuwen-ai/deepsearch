import pytest

from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class TestWorkflowRegistration:
    @pytest.mark.parametrize(
        ("agent_cls", "research_name", "builder_name"),
        [
            ("DeepresearchAgent", "research_workflow", "_build_research_workflow"),
            (
                "DeepresearchDependencyAgent",
                "research_workflow_dependency_driving",
                "_build_research_dependency_workflow",
            ),
        ],
    )
    def test_workflow_contains_user_feedback_processor_node(self, agent_cls, research_name, builder_name):
        from openjiuwen_deepsearch.framework.openjiuwen.agent import workflow

        agent_type = getattr(workflow, agent_cls)
        agent = agent_type.__new__(agent_type)
        agent.research_name = research_name
        agent.version = "1"
        agent.startnode_input_schema = {}
        flow = getattr(agent, builder_name)()
        node_ids = list(flow._internal._graph.nodes)
        assert NodeId.USER_FEEDBACK_PROCESSOR.value in node_ids
