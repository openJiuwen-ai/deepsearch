import logging

from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow

logger = logging.getLogger(__name__)


def test_build_general_subworkflow():
    '''创建通用research模式下子图节点图'''
    subworkflow = build_editor_team_workflow()
    assert isinstance(subworkflow, Workflow)


def test_editor_team_subworkflow_preserves_section_material_inputs():
    """The start schema must not filter chapter-scoped user materials."""
    subworkflow = build_editor_team_workflow()
    start_inputs = subworkflow._internal._workflow_spec.comp_configs[
        "start"
    ].io_configs.inputs_schema

    material_input_names = {
        "material_analysis",
        "material_usage_mode",
        "material_bindings",
        "bound_material_ids",
        "use_material_ids",
    }

    assert material_input_names.issubset(start_inputs)
    assert {
        name: f"${{{name}}}" for name in material_input_names
    } == {name: start_inputs[name] for name in material_input_names}
