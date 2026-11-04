from unittest.mock import MagicMock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import VLMChartGeneratorNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Report
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


@pytest.mark.parametrize("chart_output", [
    {"skip_node": True},
    {"error_msg": "chart generation failed"},
    {"modified_report": "# Report\n\n(#insertChart:chart_1)", "chart_messages": [{"chart_id": "chart_1"}]},
])
def test_chart_node_publishes_report_before_feedback(chart_output):
    """Publish the Markdown even when charts are disabled or fail."""
    report = Report(report_content="# Report\n\nEvidence-backed body.")
    session = MagicMock()
    session.get_global_state.side_effect = lambda key: (
        report if key == "search_context.current_report" else None
    )
    expected = chart_output.get("modified_report", report.report_content)
    with patch("openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.add_debug_log_wrapper"):
        result = VLMChartGeneratorNode()._post_handle(
            {}, {"vlm_chart_generator_output": chart_output, "current_inputs": {}}, session, None,
        )
    assert result == {"next_node": NodeId.USER_FEEDBACK_PROCESSOR.value}
    updates = {}
    for call in session.update_global_state.call_args_list:
        updates.update(call.args[0])
    assert updates["search_context.final_result.response_content"] == expected
