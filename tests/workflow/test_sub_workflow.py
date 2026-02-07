import logging

from openjiuwen.core.workflow.base import Workflow

from jiuwen_deepsearch.framework.jiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow

logger = logging.getLogger(__name__)


def test_build_general_subworkflow():
    '''创建通用research模式下子图节点图'''
    subworkflow = build_editor_team_workflow()
    assert isinstance(subworkflow, Workflow)
