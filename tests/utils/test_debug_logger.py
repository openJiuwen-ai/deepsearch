from unittest.mock import MagicMock

import pytest

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
import jiuwen_deepsearch.utils.debug_utils.debug_logger as test_logger
from jiuwen_deepsearch.utils.debug_utils.debug_logger import NodeType


def test_validate_and_normalize_path():
    assert test_logger.validate_and_normalize_path("") is None


def test_validate_and_normalize_path_exception():
    with pytest.raises(CustomValueException) as exception:
        test_logger.validate_and_normalize_path("```/aa/")
    assert exception.value.error_code == StatusCode.PARAM_CHECK_ERROR_LOG_DIR_UNSAFE.code


def test_add_debug_log(mocker):
    mocker.patch("jiuwen_deepsearch.utils.debug_utils.debug_logger.debug_enable", True)
    mock_logger = MagicMock()
    mocker.patch("jiuwen_deepsearch.utils.debug_utils.debug_logger.debug_logger", mock_logger)
    test_logger.add_debug_log("test_pre_step", "test_cur_step", 0,
                              test_logger.LogType.INPUT.value, test_logger.NodeType.SUB.value, "test")
    mock_logger.debug.assert_called_once()


def test_add_debug_log_wrapper(mocker):
    runtime_mock = MagicMock()
    mocker.patch("jiuwen_deepsearch.utils.debug_utils.debug_logger.debug_enable", True)
    mock_add_debug_log = mocker.patch("jiuwen_deepsearch.utils.debug_utils.debug_logger.add_debug_log")
    test_logger.add_debug_log_wrapper(runtime_mock, "test", 0,
                                      NodeType.SUB.value, input_content="test", output_content="test")
    mock_add_debug_log.assert_called()

