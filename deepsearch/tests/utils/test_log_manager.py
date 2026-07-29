import logging
import os
import sys
from pathlib import Path

import pytest

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler
from openjiuwen_deepsearch.utils.log_utils.log_common import DEFAULT_MAX_LOG_MESSAGE_LENGTH
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


@pytest.fixture
def clean_logs(tmp_path):
    safe_base = tmp_path / "logs"
    safe_base.mkdir(parents=True)
    LogManager._SAFE_BASE = str(safe_base)
    LogManager._initialized = False
    third_party_states = {
        logger_name: (
            logging.getLogger(logger_name).disabled,
            logging.getLogger(logger_name).propagate,
            logging.getLogger(logger_name).level,
        )
        for logger_name in LogManager._THIRD_PARTY_LOGGERS
    }
    yield safe_base
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        handler.flush()
        handler.close()
    root_logger.handlers.clear()
    for logger_name, (disabled, propagate, level) in third_party_states.items():
        logger_obj = logging.getLogger(logger_name)
        logger_obj.disabled = disabled
        logger_obj.propagate = propagate
        logger_obj.setLevel(level)
    LogManager._initialized = False


def _flush_root_handlers():
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.flush()


def _read_common_log(log_root: Path) -> str:
    common_log = log_root / "common" / "common.log"
    if not common_log.exists():
        return ""
    return common_log.read_text(encoding="utf-8")


def test_safe_log_dir_valid(clean_logs):
    target = clean_logs / "sub"
    target.mkdir()
    result = LogManager._safe_log_dir(str(target))
    assert result == str(target.resolve())


def test_safe_log_dir_invalid_not_subdir(clean_logs):
    parent = Path(clean_logs).parent
    outside = parent / "not_inside"
    outside.mkdir()

    with pytest.raises(CustomValueException) as e:
        LogManager._safe_log_dir(str(outside))

    assert str(StatusCode.PARAM_CHECK_ERROR_LOG_DIR_UNSAFE.code) in str(e.value)


def test_safe_log_dir_invalid_path(clean_logs):
    # 非法路径：resolve() 会失败
    with pytest.raises(CustomValueException):
        LogManager._safe_log_dir("/???/illegal_path")


def test_logmanager_init_once(clean_logs, monkeypatch):
    """测试 init 只执行一次"""
    LogManager._initialized = False

    # mock setup 函数，确保它们被调用一次
    called = {"common": 0, "metrics": 0, "interface": 0}

    def mock_common(*args, **kwargs):
        called["common"] += 1

    def mock_metrics(*args, **kwargs):
        called["metrics"] += 1

    def mock_interface(*args, **kwargs):
        called["interface"] += 1

    monkeypatch.setattr("openjiuwen_deepsearch.utils.log_utils.log_manager.setup_common_logger", mock_common)
    monkeypatch.setattr("openjiuwen_deepsearch.utils.log_utils.log_manager.setup_metrics_logger", mock_metrics)
    monkeypatch.setattr("openjiuwen_deepsearch.utils.log_utils.log_manager.setup_interface_logger", mock_interface)

    log_dir = str(clean_logs / "sub")
    LogManager.init(log_dir=log_dir, is_sensitive=False)

    LogManager.init(log_dir=log_dir, is_sensitive=True)

    assert called["common"] == 1
    assert called["metrics"] == 1
    assert called["interface"] == 1

    assert LogManager.is_sensitive() is False


def test_is_sensitive_set(clean_logs):
    LogManager._initialized = False
    LogManager.init(log_dir=str(clean_logs), is_sensitive=True)
    assert LogManager.is_sensitive() is True


def test_init_validation_errors(clean_logs):
    """测试 LogManager.init 的各类参数校验失败场景"""

    LogManager._initialized = False

    test_cases = [
        # is_sensitive 类型错误
        dict(
            kwargs={"is_sensitive": "not_bool"},
            expected_code=200020,
        ),

        # level 类型错误
        dict(
            kwargs={"level": 123},
            expected_code=200005,
        ),
        # level 范围错误
        dict(
            kwargs={"level": "OTHER_LEVEL"},
            expected_code=200014,
        ),

        # max_bytes 类型错误
        dict(
            kwargs={"max_bytes": "100MB"},
            expected_code=200005,
        ),
        # max_bytes 数值过小
        dict(
            kwargs={"max_bytes": -1},
            expected_code=200025,
        ),
        # max_bytes 数值过大
        dict(
            kwargs={"max_bytes": 2000 * 1024 * 1024},
            expected_code=200025,
        ),

        # backup_count 类型错误
        dict(
            kwargs={"backup_count": 10.5},
            expected_code=200005,
        ),
        # backup_count 数值负数
        dict(
            kwargs={"backup_count": -1},
            expected_code=200025,
        ),
        # backup_count 数值过大
        dict(
            kwargs={"backup_count": 1001},
            expected_code=200025,
        ),
    ]

    for case in test_cases:
        LogManager._initialized = False

        params = {
            "log_dir": str(clean_logs / "sub"),
        }
        params.update(case["kwargs"])

        with pytest.raises(CustomValueException) as exc:
            LogManager.init(**params)

        assert exc.value.error_code == case["expected_code"]


def test_safe_log_dir_sets_permission(clean_logs):
    """测试安全路径验证能正确设置目录权限"""
    target = clean_logs / "new_sub_dir"
    result_path = Path(LogManager._safe_log_dir(str(target)))

    assert result_path.exists()

    if sys.platform == "win32":
        # 在Windows上，验证目录可写（非只读）的
        assert not os.access(result_path, os.W_OK) == False
        return
    else:
        # 在Linux进行精确的权限断言
        mode = result_path.stat().st_mode & 0o777
        assert mode == 0o750, f"Expected mode 0o750, got {oct(mode)}"


def test_safe_rotating_file_handler_permissions(clean_logs):
    """测试SafeRotatingFileHandler能否正确设置文件和目录权限"""
    log_file = clean_logs / "test_dir" / "test.log"
    handler = SafeRotatingFileHandler(
        filename=str(log_file),
        maxBytes=1024,
        backupCount=2,
        delay=True
    )

    # 首次写入，验证目录和当前文件权限
    logger = logging.getLogger("test_perm")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("First message")
    log_dir = log_file.parent

    for i in range(50):
        logger.info(f"Message {i} to fill log")

    if sys.platform == "win32":
        # Windows: 验证创建、写入
        assert log_dir.exists()
        assert log_file.exists()
        assert os.access(log_dir, os.W_OK)  # 检查目录可写
        print("Windows: 跳过POSIX权限检查，验证文件和目录创建、轮转逻辑。")
    else:
        # Linux: 精确的权限断言
        dir_mode = log_dir.stat().st_mode & 0o777
        assert dir_mode == 0o750, f"目录权限不符: 期望 0o750, 实际 {oct(dir_mode)}"

        file_mode = log_file.stat().st_mode & 0o777
        assert file_mode == 0o640, f"活跃日志文件权限不符: 期望 0o640, 实际 {oct(file_mode)}"

        handler.doRollover()
        for i in range(1, handler.backupCount + 1):
            backup = Path(f"{log_file}.{i}")
            if backup.exists():
                backup_mode = backup.stat().st_mode & 0o777
                assert backup_mode == 0o440, f"备份文件 {i} 权限不符: 期望 0o440, 实际 {oct(backup_mode)}"

    handler.close()


def test_common_log_truncates_long_message(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.test_log")
    long_message = "HEAD" * 500 + "BODY" * 800 + "TAIL" * 500

    logger.info(long_message)
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert "truncated, original_len=" in common_log_text
    assert "HEADHEADHEAD" in common_log_text
    assert "TAILTAILTAIL" in common_log_text
    assert long_message not in common_log_text


def test_common_log_keeps_boundary_message_without_truncation(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.boundary")
    boundary_message = "a" * DEFAULT_MAX_LOG_MESSAGE_LENGTH

    logger.info(boundary_message)
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert boundary_message in common_log_text
    assert "truncated, original_len=" not in common_log_text


def test_skip_truncation_preserves_full_message(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.key_log")
    long_message = "IMPORTANT-" + ("0123456789" * 700)

    logger.info(long_message, extra={"skip_truncation": True})
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert long_message in common_log_text
    assert "truncated, original_len=" not in common_log_text


def test_exception_log_truncates_message_and_keeps_traceback(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.exception_log")

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("X" * (DEFAULT_MAX_LOG_MESSAGE_LENGTH + 200))
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert "truncated, original_len=" in common_log_text
    assert "Traceback (most recent call last)" in common_log_text
    assert "ValueError: boom" in common_log_text


def test_third_party_debug_info_are_filtered_but_warning_error_are_kept(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    for logger_name in LogManager._THIRD_PARTY_LOGGERS:
        logger_obj = logging.getLogger(logger_name)
        assert logger_obj.disabled is False
        assert logger_obj.propagate is True
        assert logger_obj.level == logging.WARNING

    third_party_logger = logging.getLogger("openai._base_client")
    third_party_logger.info("third-party-info-should-not-appear")
    third_party_logger.warning("third-party-warning-should-appear")
    third_party_logger.error("third-party-error-should-appear")
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert "third-party-info-should-not-appear" not in common_log_text
    assert "third-party-warning-should-appear" in common_log_text
    assert "third-party-error-should-appear" in common_log_text


def test_project_logger_is_allowed_to_write_common_log(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("server.test_module")

    logger.warning("project-warning-should-appear")
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert "project-warning-should-appear" in common_log_text


def test_representative_key_log_can_bypass_truncation(clean_logs):
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger(
        "openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research"
    )
    full_result_text = "=============== result text =================:\n" + ("RESULT-" * 900)

    logger.info(full_result_text, extra={"skip_truncation": True})
    _flush_root_handlers()

    common_log_text = _read_common_log(clean_logs)
    assert full_result_text in common_log_text
