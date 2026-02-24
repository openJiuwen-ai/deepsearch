from unittest.mock import MagicMock

import jiuwen_deepsearch.utils.debug_utils.node_debug as test_logger
from jiuwen_deepsearch.utils.debug_utils.node_debug import NodeType


def test_add_debug_log(mocker):
    mock_logger = MagicMock()
    mocker.patch("logging.getLogger", return_value=mock_logger)
    test_logger.record_node_debug_log(test_logger.NodeDebugLogRecord(
        "test_pre_step", "test_cur_step", 0, test_logger.LogType.INPUT.value, test_logger.NodeType.SUB.value, "test"
    ))
    mock_logger.debug.assert_called_once()


def test_add_debug_log_wrapper(mocker):
    session_mock = MagicMock()
    mock_add_debug_log = mocker.patch("jiuwen_deepsearch.utils.debug_utils.node_debug.record_node_debug_log")
    test_logger.add_debug_log_wrapper(session_mock, test_logger.NodeDebugData(
        "test", 0, NodeType.SUB.value, input_content="test", output_content="test"
    ))
    mock_add_debug_log.assert_called()
