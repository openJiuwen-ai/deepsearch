import asyncio
import datetime
import logging
import os
import shutil
import sys
from pathlib import Path

import pytest

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_handlers import SafeRotatingFileHandler
from openjiuwen_deepsearch.utils.log_utils.log_common import (
    DEFAULT_MAX_LOG_MESSAGE_LENGTH,
    RotationConfig,
    run_id_ctx,
    session_id_ctx,
)
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
    # 清理 propagate=False 的独立 logger 上的 handler
    for logger_name in ("metrics", "deepsearch_interface"):
        logger_obj = logging.getLogger(logger_name)
        for handler in list(logger_obj.handlers):
            handler.flush()
            handler.close()
        logger_obj.handlers.clear()
    for logger_name, (disabled, propagate, level) in third_party_states.items():
        logger_obj = logging.getLogger(logger_name)
        logger_obj.disabled = disabled
        logger_obj.propagate = propagate
        logger_obj.setLevel(level)
    LogManager._active_run_handlers.clear()
    LogManager._initialized = False


def _flush_root_handlers():
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.flush()


def _list_common_logs(common_dir: Path):
    """列出 common 日志文件 (排除 common_warning_*.log)"""
    return [
        p for p in common_dir.rglob("common_*.log")
        if not p.name.startswith("common_warning_")
    ]


def _read_common_log(log_root: Path) -> str:
    """读取 per-run common 日志文件内容。

    日志路径为 common/YYYYMMDD/common_YYYYMMDD_HHMMSS_hash.log,
    查找最新的 common_*.log 文件 (排除 common_warning_*.log)。
    """
    common_dir = log_root / "common"
    if not common_dir.exists():
        return ""
    candidates = _list_common_logs(common_dir)
    if not candidates:
        return ""
    # 取最新修改的文件 (mtime 并列时按文件名决定, 保证确定性)
    latest = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
    return latest.read_text(encoding="utf-8")


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

        # rotation.max_bytes 类型错误
        dict(
            kwargs={"rotation": RotationConfig(max_bytes="100MB")},
            expected_code=200005,
        ),
        # rotation.max_bytes 数值过小
        dict(
            kwargs={"rotation": RotationConfig(max_bytes=-1)},
            expected_code=200025,
        ),
        # rotation.max_bytes 数值过大
        dict(
            kwargs={"rotation": RotationConfig(max_bytes=2000 * 1024 * 1024)},
            expected_code=200025,
        ),

        # rotation.backup_count 类型错误
        dict(
            kwargs={"rotation": RotationConfig(backup_count=10.5)},
            expected_code=200005,
        ),
        # rotation.backup_count 数值负数
        dict(
            kwargs={"rotation": RotationConfig(backup_count=-1)},
            expected_code=200025,
        ),
        # rotation.backup_count 数值过大
        dict(
            kwargs={"rotation": RotationConfig(backup_count=1001)},
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


def test_common_log_uses_date_folder_and_per_run_filename(clean_logs):
    """测试 common 日志文件路径包含日期文件夹和 per-run 文件名"""
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.test_path")
    logger.info("test message for path verification")
    logger.warning("test warning message")
    _flush_root_handlers()

    common_dir = clean_logs / "common"
    assert common_dir.exists()

    # 查找日期文件夹 (YYYYMMDD)
    date_dirs = [d for d in common_dir.iterdir() if d.is_dir() and len(d.name) == 8 and d.name.isdigit()]
    assert len(date_dirs) == 1, f"Expected 1 date folder, got {date_dirs}"

    # 查找 per-run 日志文件 (排除 common_warning_*.log)
    log_files = _list_common_logs(date_dirs[0])
    assert len(log_files) == 1, f"Expected 1 common log file, got {log_files}"
    filename = log_files[0].name
    # 验证文件名格式: common_YYYYMMDD_HHMMSS_hash.log
    assert filename.startswith("common_")
    assert filename.endswith(".log")
    parts = filename[len("common_"):-len(".log")].split("_")
    assert len(parts) == 3, f"Expected 3 parts (date_time_hash), got {parts}"
    assert len(parts[0]) == 8  # YYYYMMDD
    assert len(parts[1]) == 6  # HHMMSS
    assert len(parts[2]) == 8  # hash

    # 验证 warning 日志文件也在同一日期文件夹
    warning_files = list(date_dirs[0].glob("common_warning_*.log"))
    assert len(warning_files) == 1, f"Expected 1 warning log file, got {warning_files}"


def test_new_run_creates_new_log_file(clean_logs):
    """测试 new_run() + end_run() 创建 per-run handler,日志按 run_id 隔离"""
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.test_new_run")

    # 第一次运行
    run_id_1 = LogManager.new_run()
    assert run_id_1  # 应返回非空 run_id
    token_1 = run_id_ctx.set(run_id_1)
    logger.info("first run message")
    _flush_root_handlers()
    run_id_ctx.reset(token_1)
    LogManager.end_run(run_id_1)

    # 第二次运行
    run_id_2 = LogManager.new_run()
    assert run_id_2
    token_2 = run_id_ctx.set(run_id_2)
    logger.info("second run message")
    _flush_root_handlers()
    run_id_ctx.reset(token_2)
    LogManager.end_run(run_id_2)

    # 查找所有 common 日志文件 (init + 2 per-run = 3)
    all_logs = _list_common_logs(clean_logs / "common")
    assert len(all_logs) == 3, f"Expected 3 log files (1 init + 2 per-run), got {len(all_logs)}"

    # 找到包含各自消息的文件,验证隔离性
    first_files = [f for f in all_logs if "first run message" in f.read_text(encoding="utf-8")]
    second_files = [f for f in all_logs if "second run message" in f.read_text(encoding="utf-8")]
    # init 文件包含两条 (它捕获所有日志),per-run 文件各含一条
    assert len(first_files) >= 1
    assert len(second_files) >= 1

    # per-run 文件不应同时包含两条消息 (隔离性)
    isolated_files = [f for f in all_logs if (
        "first run message" in f.read_text(encoding="utf-8")
        and "second run message" not in f.read_text(encoding="utf-8")
    )]
    assert len(isolated_files) >= 1, "Should have at least 1 file with only first run message"

    isolated_files_2 = [f for f in all_logs if (
        "second run message" in f.read_text(encoding="utf-8")
        and "first run message" not in f.read_text(encoding="utf-8")
    )]
    assert len(isolated_files_2) >= 1, "Should have at least 1 file with only second run message"

    # end_run 后 handler 应被清理
    assert run_id_1 not in LogManager._active_run_handlers
    assert run_id_2 not in LogManager._active_run_handlers


def test_new_run_preserves_metrics_and_interface_handlers(clean_logs):
    """测试 new_run() 在 metrics logger 上 ADD per-run handler,不影响 interface handler"""
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    # metrics 和 interface logger 均设置了 propagate=False, handler 挂在各自 logger 上
    metrics_logger = logging.getLogger("metrics")
    interface_logger = logging.getLogger("deepsearch_interface")
    assert metrics_logger.propagate is False
    assert interface_logger.propagate is False
    metrics_handlers_before = list(metrics_logger.handlers)
    interface_handlers_before = list(interface_logger.handlers)
    assert len(metrics_handlers_before) > 0
    assert len(interface_handlers_before) > 0

    # 记录 init 后 root logger 上的 SafeRotatingFileHandler 数量 (init: 2 = common + warning)
    root_handlers_before = [h for h in logging.getLogger().handlers if isinstance(h, SafeRotatingFileHandler)]
    assert len(root_handlers_before) == 2

    run_id = LogManager.new_run()

    # interface 的 handler 应保持不变 (new_run 不管理 interface logger)
    assert list(interface_logger.handlers) == interface_handlers_before

    # metrics logger 上 ADD 了 1 个 per-run handler (init: 1, new_run 后: 2)
    metrics_handlers_after = list(metrics_logger.handlers)
    assert len(metrics_handlers_after) == len(metrics_handlers_before) + 1

    # new_run 在 root logger 上 ADD 了 2 个 per-run handler (不替换)
    root_handlers_after = [h for h in logging.getLogger().handlers if isinstance(h, SafeRotatingFileHandler)]
    assert len(root_handlers_after) == 4, f"Expected 4 (2 init + 2 per-run), got {len(root_handlers_after)}"

    # end_run 后应恢复到 init 时的数量
    LogManager.end_run(run_id)
    root_handlers_end = [h for h in logging.getLogger().handlers if isinstance(h, SafeRotatingFileHandler)]
    assert len(root_handlers_end) == 2
    metrics_handlers_end = list(metrics_logger.handlers)
    assert len(metrics_handlers_end) == len(metrics_handlers_before)


def test_new_run_without_init_is_noop(clean_logs):
    """测试未 init 时调用 new_run() 不报错"""
    LogManager._initialized = False
    # 应该安全返回, 不抛异常
    LogManager.new_run()


def test_new_run_with_stream_handler_is_noop(clean_logs):
    """测试 StreamHandler 模式 (log_dir=None) 下 new_run() 是 no-op"""
    LogManager._initialized = False
    LogManager.init(log_dir=None, is_sensitive=False)
    # 应该安全返回, 不创建文件
    LogManager.new_run()


def test_server_mode_sequential_creates_per_run_files(clean_logs):
    """测试 Server 模式 (每个请求调用 new_run/end_run) 下多次顺序请求各自创建 per-run 文件"""
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    logger = logging.getLogger("openjiuwen_deepsearch.test_server_mode")

    # 模拟 Server 顺序处理两个请求 (各调用 new_run/end_run)
    for msg in ("request 1 log", "request 2 log"):
        run_id = LogManager.new_run()
        token = run_id_ctx.set(run_id)
        try:
            logger.info(msg)
            _flush_root_handlers()
        finally:
            run_id_ctx.reset(token)
            LogManager.end_run(run_id)

    # 应有 3 个日志文件: 1 init + 2 per-run
    all_logs = _list_common_logs(clean_logs / "common")
    assert len(all_logs) == 3, f"Expected 3 log files (1 init + 2 per-run), got {len(all_logs)}"

    # init 文件包含两条日志
    init_files = [f for f in all_logs if "request 1 log" in f.read_text(encoding="utf-8") and "request 2 log" in f.read_text(encoding="utf-8")]
    assert len(init_files) == 1, "init file should contain both requests' logs"

    # per-run 文件各自隔离
    r1_only = [f for f in all_logs if "request 1 log" in f.read_text(encoding="utf-8") and "request 2 log" not in f.read_text(encoding="utf-8")]
    assert len(r1_only) == 1, "Should have 1 file with only request 1"
    r2_only = [f for f in all_logs if "request 2 log" in f.read_text(encoding="utf-8") and "request 1 log" not in f.read_text(encoding="utf-8")]
    assert len(r2_only) == 1, "Should have 1 file with only request 2"


def test_server_mode_concurrent_per_run_isolation(clean_logs):
    """测试 Server 模式下并发请求通过 per-run handler 隔离日志"""
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    async def mock_server_request(msg):
        """模拟 Server 请求: new_run → set run_id/session_id → 写日志 → end_run"""
        run_id = LogManager.new_run()
        token = run_id_ctx.set(run_id)
        sid_token = session_id_ctx.set(f"sid-{msg}")
        try:
            logger = logging.getLogger(f"openjiuwen_deepsearch.test.server.{msg}")
            logger.info(f"{msg} start")
            await asyncio.sleep(0.01)  # 让出控制权,另一个请求开始
            logger.info(f"{msg} end")
            logger.warning(f"{msg} warn")
            # metrics logger (propagate=False) 写入打点日志
            logging.getLogger("metrics").info(f"[TIME_STATS] {msg} metric")
        finally:
            session_id_ctx.reset(sid_token)
            run_id_ctx.reset(token)
            LogManager.end_run(run_id)

    async def run_concurrent():
        await asyncio.gather(mock_server_request("req1"), mock_server_request("req2"))

    asyncio.run(run_concurrent())
    _flush_root_handlers()
    # metrics logger propagate=False,需单独 flush
    for h in logging.getLogger("metrics").handlers:
        h.flush()

    # per-run handler 应已清理
    assert len(LogManager._active_run_handlers) == 0

    # 应有 3 个日志文件: 1 init + 2 per-run
    all_logs = _list_common_logs(clean_logs / "common")
    assert len(all_logs) == 3, f"Expected 3 log files (1 init + 2 per-run), got {len(all_logs)}"

    # req1 的 per-run 文件只含 req1 日志
    r1_isolated = [
        f for f in all_logs
        if "req1 start" in f.read_text(encoding="utf-8")
        and "req2" not in f.read_text(encoding="utf-8")
    ]
    assert len(r1_isolated) == 1, "Should have exactly 1 file with only req1 logs"

    # req2 的 per-run 文件只含 req2 日志
    r2_isolated = [
        f for f in all_logs
        if "req2 start" in f.read_text(encoding="utf-8")
        and "req1" not in f.read_text(encoding="utf-8")
    ]
    assert len(r2_isolated) == 1, "Should have exactly 1 file with only req2 logs"

    # init 文件包含两个请求的日志
    init_file = [
        f for f in all_logs
        if "req1 start" in f.read_text(encoding="utf-8")
        and "req2 start" in f.read_text(encoding="utf-8")
    ]
    assert len(init_file) == 1, "init file should contain both requests' logs"

    # per-run 文件应注入各自的 session_id (SessionFilter)
    r1_session = [
        f for f in all_logs
        if "session_id=sid-req1" in f.read_text(encoding="utf-8")
        and "session_id=sid-req2" not in f.read_text(encoding="utf-8")
    ]
    assert len(r1_session) == 1, "Should have exactly 1 file with req1 session_id only"

    r2_session = [
        f for f in all_logs
        if "session_id=sid-req2" in f.read_text(encoding="utf-8")
        and "session_id=sid-req1" not in f.read_text(encoding="utf-8")
    ]
    assert len(r2_session) == 1, "Should have exactly 1 file with req2 session_id only"

    # per-run warning 文件同样按 run_id 隔离
    warning_logs = list((clean_logs / "common").rglob("common_warning_*.log"))
    w1_isolated = [
        f for f in warning_logs
        if "req1 warn" in f.read_text(encoding="utf-8")
        and "req2" not in f.read_text(encoding="utf-8")
    ]
    assert len(w1_isolated) == 1, "Should have exactly 1 warning file with only req1"

    w2_isolated = [
        f for f in warning_logs
        if "req2 warn" in f.read_text(encoding="utf-8")
        and "req1" not in f.read_text(encoding="utf-8")
    ]
    assert len(w2_isolated) == 1, "Should have exactly 1 warning file with only req2"

    w_init = [
        f for f in warning_logs
        if "req1 warn" in f.read_text(encoding="utf-8")
        and "req2 warn" in f.read_text(encoding="utf-8")
    ]
    assert len(w_init) == 1, "init warning file should contain both requests' warns"

    # per-run metrics 文件同样按 run_id 隔离
    metrics_logs = list((clean_logs / "metrics").rglob("metrics_*.log"))
    # 应有 3 个 metrics 文件: 1 init + 2 per-run
    assert len(metrics_logs) == 3, f"Expected 3 metrics files, got {len(metrics_logs)}"
    m1_isolated = [
        f for f in metrics_logs
        if "req1 metric" in f.read_text(encoding="utf-8")
        and "req2" not in f.read_text(encoding="utf-8")
    ]
    assert len(m1_isolated) == 1, "Should have exactly 1 metrics file with only req1"
    m2_isolated = [
        f for f in metrics_logs
        if "req2 metric" in f.read_text(encoding="utf-8")
        and "req1" not in f.read_text(encoding="utf-8")
    ]
    assert len(m2_isolated) == 1, "Should have exactly 1 metrics file with only req2"
    m_init = [
        f for f in metrics_logs
        if "req1 metric" in f.read_text(encoding="utf-8")
        and "req2 metric" in f.read_text(encoding="utf-8")
    ]
    assert len(m_init) == 1, "init metrics file should contain both requests' metrics"


def test_sdk_concurrent_per_run_isolation(clean_logs):
    """测试 SDK 并发调用时 per-run handler 按 run_id 隔离日志"""
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    async def mock_workflow(msg):
        """模拟 run_jiuwen_workflow: new_run → set run_id/session_id → 写日志 → end_run"""
        run_id = LogManager.new_run()
        token = run_id_ctx.set(run_id)
        sid_token = session_id_ctx.set(f"sid-{msg}")
        try:
            logger = logging.getLogger(f"openjiuwen_deepsearch.test.concurrent.{msg}")
            logger.info(f"{msg} start")
            await asyncio.sleep(0.01)  # 让出控制权,另一个协程开始
            logger.info(f"{msg} end")
            logger.warning(f"{msg} warn")
            # metrics logger (propagate=False) 写入打点日志
            logging.getLogger("metrics").info(f"[TIME_STATS] {msg} metric")
        finally:
            session_id_ctx.reset(sid_token)
            run_id_ctx.reset(token)
            LogManager.end_run(run_id)

    async def run_concurrent():
        await asyncio.gather(mock_workflow("taskA"), mock_workflow("taskB"))

    asyncio.run(run_concurrent())
    _flush_root_handlers()
    # metrics logger propagate=False,需单独 flush
    for h in logging.getLogger("metrics").handlers:
        h.flush()

    # 两个 per-run handler 都应被清理
    assert len(LogManager._active_run_handlers) == 0

    # 应有 3 个 common 日志文件: 1 init + 2 per-run
    all_logs = _list_common_logs(clean_logs / "common")
    assert len(all_logs) == 3, f"Expected 3 log files (1 init + 2 per-run), got {len(all_logs)}"

    # 找到 taskA 的 per-run 文件 (只含 taskA, 不含 taskB)
    a_isolated = [
        f for f in all_logs
        if "taskA start" in f.read_text(encoding="utf-8")
        and "taskB" not in f.read_text(encoding="utf-8")
    ]
    assert len(a_isolated) == 1, "Should have exactly 1 file with only taskA logs"

    # 找到 taskB 的 per-run 文件 (只含 taskB, 不含 taskA)
    b_isolated = [
        f for f in all_logs
        if "taskB start" in f.read_text(encoding="utf-8")
        and "taskA" not in f.read_text(encoding="utf-8")
    ]
    assert len(b_isolated) == 1, "Should have exactly 1 file with only taskB logs"

    # init 文件应包含两个任务的日志
    init_file = [
        f for f in all_logs
        if "taskA start" in f.read_text(encoding="utf-8")
        and "taskB start" in f.read_text(encoding="utf-8")
    ]
    assert len(init_file) == 1, "init file should contain both tasks' logs"

    # per-run 文件应注入各自的 session_id (SessionFilter)
    a_session = [
        f for f in all_logs
        if "session_id=sid-taskA" in f.read_text(encoding="utf-8")
        and "session_id=sid-taskB" not in f.read_text(encoding="utf-8")
    ]
    assert len(a_session) == 1, "Should have exactly 1 file with taskA session_id only"

    b_session = [
        f for f in all_logs
        if "session_id=sid-taskB" in f.read_text(encoding="utf-8")
        and "session_id=sid-taskA" not in f.read_text(encoding="utf-8")
    ]
    assert len(b_session) == 1, "Should have exactly 1 file with taskB session_id only"

    # per-run warning 文件同样按 run_id 隔离
    warning_logs = list((clean_logs / "common").rglob("common_warning_*.log"))
    wa_isolated = [
        f for f in warning_logs
        if "taskA warn" in f.read_text(encoding="utf-8")
        and "taskB" not in f.read_text(encoding="utf-8")
    ]
    assert len(wa_isolated) == 1, "Should have exactly 1 warning file with only taskA"

    wb_isolated = [
        f for f in warning_logs
        if "taskB warn" in f.read_text(encoding="utf-8")
        and "taskA" not in f.read_text(encoding="utf-8")
    ]
    assert len(wb_isolated) == 1, "Should have exactly 1 warning file with only taskB"

    w_init = [
        f for f in warning_logs
        if "taskA warn" in f.read_text(encoding="utf-8")
        and "taskB warn" in f.read_text(encoding="utf-8")
    ]
    assert len(w_init) == 1, "init warning file should contain both tasks' warns"

    # per-run metrics 文件同样按 run_id 隔离
    metrics_logs = list((clean_logs / "metrics").rglob("metrics_*.log"))
    # 应有 3 个 metrics 文件: 1 init + 2 per-run
    assert len(metrics_logs) == 3, f"Expected 3 metrics files, got {len(metrics_logs)}"
    ma_isolated = [
        f for f in metrics_logs
        if "taskA metric" in f.read_text(encoding="utf-8")
        and "taskB" not in f.read_text(encoding="utf-8")
    ]
    assert len(ma_isolated) == 1, "Should have exactly 1 metrics file with only taskA"
    mb_isolated = [
        f for f in metrics_logs
        if "taskB metric" in f.read_text(encoding="utf-8")
        and "taskA" not in f.read_text(encoding="utf-8")
    ]
    assert len(mb_isolated) == 1, "Should have exactly 1 metrics file with only taskB"
    m_init = [
        f for f in metrics_logs
        if "taskA metric" in f.read_text(encoding="utf-8")
        and "taskB metric" in f.read_text(encoding="utf-8")
    ]
    assert len(m_init) == 1, "init metrics file should contain both tasks' metrics"


def test_cleanup_old_logs_removes_expired_dirs(clean_logs):
    """测试 _cleanup_old_logs 删除超过保留天数的日志目录"""
    LogManager._initialized = False
    LogManager._log_retention_days = 30
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    # 创建一个 31 天前的 common 日期文件夹 (应被删除)
    old_date = (datetime.date.today() - datetime.timedelta(days=31)).strftime("%Y%m%d")
    old_common_dir = clean_logs / "common" / old_date
    old_common_dir.mkdir(parents=True)
    (old_common_dir / "common_old.log").write_text("old", encoding="utf-8")

    # 创建一个 31 天前的 metrics 日期文件夹 (应被删除)
    old_metrics_dir = clean_logs / "metrics" / old_date
    old_metrics_dir.mkdir(parents=True)
    (old_metrics_dir / "metrics_old.log").write_text("old", encoding="utf-8")

    # 创建一个 10 天前的 common 日期文件夹 (应保留)
    recent_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y%m%d")
    recent_common_dir = clean_logs / "common" / recent_date
    recent_common_dir.mkdir(parents=True)
    (recent_common_dir / "common_recent.log").write_text("recent", encoding="utf-8")

    assert old_common_dir.exists()
    assert old_metrics_dir.exists()
    assert recent_common_dir.exists()

    LogManager._cleanup_old_logs()

    assert not old_common_dir.exists(), "Old common dir should be removed"
    assert not old_metrics_dir.exists(), "Old metrics dir should be removed"
    assert recent_common_dir.exists(), "Recent common dir should be kept"


def test_cleanup_old_logs_zero_retention_disables_cleanup(clean_logs):
    """测试 log_retention_days=0 通过 init() 参数禁用清理"""
    LogManager._initialized = False
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False, log_retention_days=0)

    old_date = (datetime.date.today() - datetime.timedelta(days=100)).strftime("%Y%m%d")
    old_common_dir = clean_logs / "common" / old_date
    old_common_dir.mkdir(parents=True)
    (old_common_dir / "common_old.log").write_text("old", encoding="utf-8")

    LogManager._cleanup_old_logs()

    assert old_common_dir.exists(), "Should not clean when retention_days=0"


def test_cleanup_triggered_by_init(clean_logs):
    """测试 init() 自动触发清理"""
    # 预先创建过期目录
    old_date = (datetime.date.today() - datetime.timedelta(days=31)).strftime("%Y%m%d")
    old_common_dir = clean_logs / "common" / old_date
    old_common_dir.mkdir(parents=True)
    (old_common_dir / "common_old.log").write_text("old", encoding="utf-8")
    assert old_common_dir.exists()

    # init 应自动清理过期日志
    LogManager._initialized = False
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    assert not old_common_dir.exists(), "init() should auto-cleanup expired dirs"


def test_cleanup_triggered_by_new_run(clean_logs):
    """测试 new_run() 自动触发清理"""
    LogManager._initialized = False
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    # init 后创建过期目录
    old_date = (datetime.date.today() - datetime.timedelta(days=31)).strftime("%Y%m%d")
    old_common_dir = clean_logs / "common" / old_date
    old_common_dir.mkdir(parents=True)
    (old_common_dir / "common_old.log").write_text("old", encoding="utf-8")
    assert old_common_dir.exists()

    # new_run 应自动清理
    run_id = LogManager.new_run()
    assert run_id, "new_run should return non-empty run_id"
    assert not old_common_dir.exists(), "new_run() should auto-cleanup expired dirs"
    LogManager.end_run(run_id)


def test_cleanup_preserves_non_date_dirs(clean_logs):
    """测试非 YYYYMMDD 格式的文件夹不会被误删"""
    LogManager._initialized = False
    LogManager._log_retention_days = 1
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)
    LogManager._log_retention_days = 1  # 覆盖为 1 天,加快过期

    # 创建非日期格式的目录
    non_date_dir = clean_logs / "common" / "not_a_date"
    non_date_dir.mkdir(parents=True)
    (non_date_dir / "dummy.log").write_text("data", encoding="utf-8")

    LogManager._cleanup_old_logs()

    assert non_date_dir.exists(), "Non-YYYYMMDD dirs should not be deleted"


def test_cleanup_preserves_today_dir(clean_logs):
    """测试当天文件夹不会被清理"""
    LogManager._initialized = False
    LogManager.init(log_dir=str(clean_logs), level="DEBUG", is_sensitive=False)

    today_str = datetime.date.today().strftime("%Y%m%d")
    today_common_dir = clean_logs / "common" / today_str
    # today dir may already exist from init, just verify it survives cleanup
    LogManager._cleanup_old_logs()

    assert today_common_dir.exists(), "Today's log dir should not be cleaned"
