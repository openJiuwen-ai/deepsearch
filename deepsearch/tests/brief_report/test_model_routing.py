"""Brief 节点复用既有模型类别的契约测试。"""

from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import NODE_LLM_MAPPING
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


def test_brief_nodes_reuse_existing_model_categories():
    """Brief 节点必须映射到既有模型类别，而不是引入新配置。"""
    assert NODE_LLM_MAPPING[NodeId.BRIEF_OUTLINE.value] == "plan_understanding"
    assert NODE_LLM_MAPPING[NodeId.BRIEF_INFO_COLLECTOR.value] == "info_collecting"
    assert NODE_LLM_MAPPING[NodeId.BRIEF_EVIDENCE_REVIEWER.value] == "info_collecting"
    assert NODE_LLM_MAPPING[NodeId.BRIEF_SUB_REPORTER.value] == "writing_checking"
    assert NODE_LLM_MAPPING[NodeId.BRIEF_REPORTER.value] == "writing_checking"
    assert NODE_LLM_MAPPING[NodeId.BRIEF_HTML_REPORTER.value] == "writing_checking"
