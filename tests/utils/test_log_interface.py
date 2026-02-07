import logging
import json
from logging.handlers import RotatingFileHandler

from jiuwen_deepsearch.utils.log_utils.log_interface import setup_interface_logger, record_interface_log


def test_setup_interface_logger_stream_handler(monkeypatch):
    """log_dir=None 时，应当配置 StreamHandler"""
    logger = logging.getLogger("deepsearch_interface")
    logger.handlers.clear()

    setup_interface_logger(log_dir=None)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_setup_interface_logger_file(tmp_path):
    """log_dir不为None时，创建文件目录与 RotatingFileHandler"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    logger = logging.getLogger("deepsearch_interface")
    logger.handlers.clear()

    setup_interface_logger(str(log_dir))

    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.baseFilename.endswith("deepsearch_interface.log")
    assert (log_dir / "interface").exists()


def test_record_interface_log_success(tmp_path):
    """测试记录成功日志"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    logger = logging.getLogger("deepsearch_interface")
    logger.handlers.clear()

    setup_interface_logger(str(log_dir))

    record_interface_log(
        role="user",
        session_id="sid123",
        api_name="test_api",
        duration_min=1.23,
        success=True,
        response_info={"key": "value"}
    )

    log_file = log_dir / "interface" / "deepsearch_interface.log"
    assert log_file.exists()

    content = log_file.read_text(encoding="utf-8")
    assert "success" in content
    assert "test_api" in content
    assert json.dumps({"key": "value"}, ensure_ascii=False) in content


def test_record_interface_log_fail(tmp_path):
    """测试失败情况"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    logger = logging.getLogger("deepsearch_interface")
    logger.handlers.clear()

    setup_interface_logger(str(log_dir))

    record_interface_log(
        role="ai",
        session_id="sid456",
        api_name="api_failed",
        duration_min=0.56,
        success=False,
        response_info={"error": "bad request"}
    )

    log_file = log_dir / "interface" / "deepsearch_interface.log"
    assert log_file.exists()

    content = log_file.read_text(encoding="utf-8")
    assert "fail" in content
    assert "api_failed" in content
    assert '"error": "bad request"' in content
