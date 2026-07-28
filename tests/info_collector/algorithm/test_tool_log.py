import pytest
from unittest.mock import patch

import httpx

from openjiuwen_deepsearch.algorithm.research_collector.tool_log import \
    is_sensitive_key, get_logged_tool, tool_invoke_log, tool_invoke_log_async
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

MODULE_PATH = "openjiuwen_deepsearch.algorithm.research_collector.tool_log"


class TestIsSensitiveKey:
    """测试 is_sensitive_key 函数"""

    def test_is_sensitive_key_positive_cases(self):
        """测试敏感键名"""
        sensitive_keys = [
            "api_key", "secret_key", "access_key", "key",
            "database_url", "redis_url", "endpoint_url",
            "auth_token", "bearer_token", "token", "url"
        ]

        for key in sensitive_keys:
            assert is_sensitive_key(key) == True, f"Key '{key}' should be sensitive"

    def test_is_sensitive_key_negative_cases(self):
        """测试非敏感键名"""
        non_sensitive_keys = [
            "name", "title", "content", "description",
            "count", "limit", "offset", "page",
            "query", "search", "filter", "sort"
        ]

        for key in non_sensitive_keys:
            assert is_sensitive_key(key) == False, f"Key '{key}' should not be sensitive"

    def test_is_sensitive_key_case_insensitive(self):
        """测试大小写不敏感"""
        assert is_sensitive_key("API_KEY") == True
        assert is_sensitive_key("Api_Key") == True
        assert is_sensitive_key("api_Key") == True
        assert is_sensitive_key("TOKEN") == True
        assert is_sensitive_key("Url") == True

    def test_is_sensitive_key_partial_matches(self):
        """测试部分匹配"""
        assert is_sensitive_key("my_api_key_here") == True
        assert is_sensitive_key("access_token_value") == True
        assert is_sensitive_key("database_url_string") == True
        assert is_sensitive_key("some_key_name") == True


class TestGetLoggedTool:
    """测试 get_logged_tool 函数"""

    def setup_method(self):
        # 创建一个基础的tool类用于测试
        class BaseTool:
            def __init__(self, name="TestTool"):
                self.name = name

            def _run(self, *args, **kwargs):
                return f"Result from {self.name} with args: {args}, kwargs: {kwargs}"

            async def _arun(self, *args, **kwargs):
                return f"Async result from {self.name} with args: {args}, kwargs: {kwargs}"

        self.BaseTool = BaseTool

    def test_get_logged_tool_creates_correct_class(self):
        """测试正确创建日志工具类"""
        LoggedTool = get_logged_tool(self.BaseTool)

        assert LoggedTool.__name__ == "BaseToolWithLogging"
        assert issubclass(LoggedTool, self.BaseTool)

        # 验证类的方法存在
        assert hasattr(LoggedTool, '_log_start')
        assert hasattr(LoggedTool, '_log_end')
        assert hasattr(LoggedTool, '_log_error')
        assert hasattr(LoggedTool, '_get_tool_name')
        assert hasattr(LoggedTool, '_run')
        assert hasattr(LoggedTool, '_arun')

    def test_get_logged_tool_format_params_non_sensitive(self):
        """测试非敏感参数的格式化"""
        LoggedTool = get_logged_tool(self.BaseTool)

        # 直接测试静态方法
        args = ("value1", "list")
        kwargs = {"name": "test", "count": "5"}

        # 通过类调用静态方法
        params = LoggedTool._format_params(args, kwargs)

        # 所有参数都应该被包含(非字符串参数会被转换为字符串)
        assert 'value1' in params
        assert "list" in params
        assert "name='test'" in params
        assert "count='5'" in params

    def test_get_logged_tool_format_params_sensitive(self):
        """测试敏感参数的过滤"""
        LoggedTool = get_logged_tool(self.BaseTool)

        args = ("api_key_value", "normal_arg")
        kwargs = {"api_key": "secret", "token": "bearer_token", "name": "test"}

        # 通过类调用静态方法
        params = LoggedTool._format_params(args, kwargs)

        # 敏感参数应该被过滤
        assert "api" not in params
        assert "key" not in params
        assert "token" not in params
        # 非敏感参数应该被保留
        assert "normal_arg" in params
        assert "name='test'" in params

    def test_get_logged_tool_truncate_result(self):
        """测试结果截断"""
        LoggedTool = get_logged_tool(self.BaseTool)

        # 短结果不截断
        short_result = "short result"
        assert LoggedTool._truncate_result(short_result) == "'short result'"

        # 长结果截断
        long_result = "a" * 150
        truncated = LoggedTool._truncate_result(long_result)
        assert len(truncated) == 103  # 100 + "..."
        assert truncated.endswith("...")

    def test_get_logged_tool_get_tool_name(self):
        """测试工具名称提取"""
        LoggedTool = get_logged_tool(self.BaseTool)
        tool_instance = LoggedTool()

        # 默认名称
        assert tool_instance._get_tool_name() == "BaseTool"

    def test_get_logged_tool_run_success(self):
        """测试同步执行成功"""
        LoggedTool = get_logged_tool(self.BaseTool)
        tool_instance = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]  # start_time, end_time
            mock_sensitive.return_value = False

            result = tool_instance._run("arg1", key1="value1")

            # 验证结果
            assert "Result from TestTool" in result

    def test_get_logged_tool_run_sensitive_mode(self):
        """测试敏感模式下的同步执行"""
        LoggedTool = get_logged_tool(self.BaseTool)
        tool_instance = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = True

            result = tool_instance._run("arg1", key1="value1")

            # 验证敏感模式下的日志
            mock_logger.info.assert_any_call("[TOOL START] BaseTool._run")
            mock_logger.info.assert_any_call("[TOOL END] BaseTool._run | Duration:  0.50s")

            # 验证结果
            assert "Result from TestTool" in result

    def test_get_logged_tool_run_exception(self):
        """测试同步执行异常"""

        class FailingTool:
            def _run(self, *args, **kwargs):
                raise ValueError("Test error")

        LoggedTool = get_logged_tool(FailingTool)
        tool_instance = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.return_value = 100.0
            mock_sensitive.return_value = False

            # 验证异常被正确抛出
            with pytest.raises(CustomValueException):
                tool_instance._run("arg1")

            # 验证错误日志
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0][0]
            assert "[TOOL ERROR] FailingTool._run | Error: Test error" in call_args

    def test_get_logged_tool_run_exception_sensitive_mode(self):
        """测试敏感模式下的同步执行异常"""

        class FailingTool:
            def _run(self, *args, **kwargs):
                raise ValueError("Test error")

        LoggedTool = get_logged_tool(FailingTool)
        tool_instance = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.return_value = 100.0
            mock_sensitive.return_value = True

            # 验证异常被正确抛出
            with pytest.raises(CustomValueException):
                tool_instance._run("arg1")

            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_once_with("[TOOL ERROR] FailingTool._run")

    @pytest.mark.asyncio
    async def test_get_logged_tool_arun_success(self):
        """测试异步执行成功"""
        LoggedTool = get_logged_tool(self.BaseTool)
        tool_instance = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = False

            result = await tool_instance._arun("arg1", key1="value1")

            # 验证结果
            assert "Async result from TestTool" in result

    @pytest.mark.asyncio
    async def test_get_logged_tool_arun_exception(self):
        """测试异步执行异常"""

        class AsyncFailingTool:
            async def _arun(self, *args, **kwargs):
                raise ValueError("Async test error")

        LoggedTool = get_logged_tool(AsyncFailingTool)
        tool_instance = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.return_value = 100.0
            mock_sensitive.return_value = False

            # 验证异常被正确抛出
            with pytest.raises(CustomValueException):
                await tool_instance._arun("arg1")

            # 验证错误日志
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0][0]
            assert "[TOOL ERROR] AsyncFailingTool._arun | Error: Async test error" in call_args


class TestToolInvokeLog:
    """测试 tool_invoke_log 装饰器"""

    def test_tool_invoke_log_success(self):
        """测试装饰器成功执行"""

        @tool_invoke_log
        def my_test_function(arg1, arg2, key1=None):
            return f"Result: {arg1}, {arg2}, {key1}"

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = False

            result = my_test_function("value1", "value2", key1="test")

            # 验证日志调用 - 使用更灵活的检查方式
            start_calls = [call for call in mock_logger.info.call_args_list
                           if "[TOOL START]" in call[0][0]]
            end_calls = [call for call in mock_logger.info.call_args_list
                         if "[TOOL END]" in call[0][0]]

            assert len(start_calls) == 1
            assert len(end_calls) == 1

            start_msg = start_calls[0][0][0]
            end_msg = end_calls[0][0][0]

            # 验证日志内容
            assert "my_test_function" in start_msg or "Start to execute tool" in start_msg
            assert "value1" in start_msg
            assert "value2" in start_msg

            assert "my_test_function" in end_msg or "Duration: " in end_msg
            assert "Result: value1, value2, test" in end_msg

            # 验证结果
            assert result == "Result: value1, value2, test"

    def test_tool_invoke_log_sensitive_args_filtering(self):
        """测试敏感参数过滤"""

        @tool_invoke_log
        def sensitive_test_function(api_key, token, normal_arg):
            return "success"

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = False

            result = sensitive_test_function("secret_key", "bearer_token", "normal_value")

            # 找到 START 日志调用
            start_calls = [call for call in mock_logger.info.call_args_list
                           if "[TOOL START]" in call[0][0]]

            assert len(start_calls) == 1
            start_msg = start_calls[0][0][0]

            print(f"DEBUG: Start message = {start_msg}")  # 调试信息

            # 验证敏感参数不在日志中
            assert "secret_key" not in start_msg
            assert "bearer_token" not in start_msg
            assert "api_key" not in start_msg
            assert "token" not in start_msg

            # 验证非敏感参数在日志中
            assert "normal_value" in start_msg

            # 验证函数正常执行
            assert result == "success"

    def test_tool_invoke_log_function_name_extraction(self):
        """测试函数名提取"""

        # 测试不同的函数名
        @tool_invoke_log
        def different_name_function():
            return "test"

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = False

            different_name_function()

            # 检查是否有任何日志调用包含函数名
            all_messages = [call[0][0] for call in mock_logger.info.call_args_list]
            function_name_in_logs = any("different_name_function" in msg for msg in all_messages)

            # 函数名应该在日志中，或者使用通用消息
            assert function_name_in_logs or any("Start to execute tool" in msg for msg in all_messages)


class TestToolInvokeLogAsync:
    """测试 tool_invoke_log_async 装饰器"""

    @pytest.mark.asyncio
    async def test_tool_invoke_log_async_success(self):
        """测试异步装饰器成功执行"""

        @tool_invoke_log_async
        async def async_test_function(arg1, arg2, key1=None):
            return f"Async Result: {arg1}, {arg2}, {key1}"

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = False

            result = await async_test_function("value1", "value2", key1="test")

            # 验证日志调用
            mock_logger.info.assert_any_call(
                "[TOOL END] async_test_function | Args: value1, value2 | Tool result count: 34 | Result content: 'Async Result: value1, value2, test' | Duration:  0.50s")

            # 验证结果
            assert result == "Async Result: value1, value2, test"

    @pytest.mark.asyncio
    async def test_tool_invoke_log_async_sensitive_mode(self):
        """测试敏感模式下的异步装饰器"""

        @tool_invoke_log_async
        async def async_test_function(arg1, arg2):
            return f"Async Result: {arg1}, {arg2}"

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = True

            result = await async_test_function("value1", "value2")

            # 验证敏感模式下的日志
            mock_logger.info.assert_any_call("[TOOL START] async_test_function")
            mock_logger.info.assert_any_call(
                "[TOOL END] async_test_function | Tool result count: 28 | Duration:  0.50s")

            # 验证结果
            assert result == "Async Result: value1, value2"

    @pytest.mark.asyncio
    async def test_tool_invoke_log_async_exception(self):
        """测试异步装饰器异常处理"""

        @tool_invoke_log_async
        async def async_failing_function():
            raise ValueError("Async test error")

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.return_value = 100.0
            mock_sensitive.return_value = False

            # 验证异常被正确抛出
            with pytest.raises(CustomValueException):
                await async_failing_function()

            # 验证错误日志
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0][0]
            assert "Exception: ValueError('Async test error')" in call_args

    @pytest.mark.asyncio
    async def test_tool_invoke_log_async_exception_sensitive_mode(self):
        """测试敏感模式下的异步装饰器异常处理"""

        @tool_invoke_log_async
        async def async_failing_function():
            raise ValueError("Async test error")

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_time.return_value = 100.0
            mock_sensitive.return_value = True

            # 验证异常被正确抛出
            with pytest.raises(CustomValueException):
                await async_failing_function()

            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_with("[TOOL ERROR] async_failing_function | Raise exception")

    @pytest.mark.asyncio
    async def test_tool_invoke_log_async_sync_function_returning_coroutine(self):
        """测试同步函数返回协程对象时，装饰器仍能正确 await"""

        async def _inner():
            return {"search_results": [{"content": "ok"}]}

        @tool_invoke_log_async
        def sync_outer():
            return _inner()

        with patch(f"{MODULE_PATH}.time.time") as mock_time, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger"):
            mock_time.side_effect = [100.0, 100.5]
            mock_sensitive.return_value = False

            result = await sync_outer()
            assert result == {"search_results": [{"content": "ok"}]}


class TestPreserveCustomException:
    """回归测试：装饰器不应吞并已有 CustomException 的 error_code。

    背景：runtime_api 工具内部对 SSRF 校验、响应大小限制、JSON 深度限制
    等场景显式抛出 ``CustomValueException(PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR
    = 200009)``，调用方依赖该错误码区分安全校验失败与普通执行错误。
    若日志装饰器无差别 ``except Exception`` 后统一包装为
    ``TOOL_EXEC_ERROR (211304)`` 或 ``TOOL_LOG_ERROR (211303)``，
    原始错误码丢失，调用方无法区分。
    """

    # ---- tool_invoke_log_async ----

    @pytest.mark.asyncio
    async def test_async_preserves_custom_value_exception_code(self):
        """被装饰函数抛出 CustomValueException(200009) 时，原样上抛。"""

        expected_code = StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code

        @tool_invoke_log_async
        async def failing_runtime_call():
            raise CustomValueException(
                error_code=expected_code,
                message="runtime api url is not allowed (private ip): '127.0.0.1'",
            )

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"), \
                patch(f"{MODULE_PATH}.Config"):
            with pytest.raises(CustomValueException) as exc_info:
                await failing_runtime_call()

        assert exc_info.value.error_code == expected_code
        assert "private ip" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_async_preserves_custom_value_exception_sensitive_mode(self):
        """敏感模式下也应原样上抛 CustomValueException，且 message 不能是方法对象。"""

        expected_code = StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code

        @tool_invoke_log_async
        async def failing_runtime_call():
            raise CustomValueException(
                error_code=expected_code,
                message="runtime api response exceeds max size 2097152 bytes",
            )

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=True), \
                patch(f"{MODULE_PATH}.logger"), \
                patch(f"{MODULE_PATH}.Config"):
            with pytest.raises(CustomValueException) as exc_info:
                await failing_runtime_call()

        assert exc_info.value.error_code == expected_code
        assert "exceeds max size" in exc_info.value.message
        assert "format" not in exc_info.value.message  # 防止 errmsg.format 漏括号

    @pytest.mark.asyncio
    async def test_async_wraps_unknown_exception_as_tool_exec_error(self):
        """被装饰函数抛出未知异常（非 CustomException）时仍包装为 TOOL_EXEC_ERROR。"""

        @tool_invoke_log_async
        async def failing_runtime_call():
            raise httpx.ConnectError("connection refused")

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"), \
                patch(f"{MODULE_PATH}.Config"):
            with pytest.raises(CustomValueException) as exc_info:
                await failing_runtime_call()

        assert exc_info.value.error_code == StatusCode.TOOL_EXEC_ERROR.code
        assert "connection refused" in exc_info.value.message
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_async_wraps_unknown_exception_sensitive_mode_message_is_string(self):
        """回归：敏感模式下包装未知异常时，message 必须是字符串。

        防止 ``StatusCode.TOOL_EXEC_ERROR.errmsg.format`` 漏括号导致 method
        对象被当作 message 传入，使 ``exc.message`` 变成
        ``<built-in method format of str object at 0x...>``。
        """

        @tool_invoke_log_async
        async def failing_runtime_call():
            raise ValueError("boom")

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=True), \
                patch(f"{MODULE_PATH}.logger"), \
                patch(f"{MODULE_PATH}.Config"):
            with pytest.raises(CustomValueException) as exc_info:
                await failing_runtime_call()

        assert exc_info.value.error_code == StatusCode.TOOL_EXEC_ERROR.code
        assert isinstance(exc_info.value.message, str)
        assert "built-in" not in exc_info.value.message
        assert "format of str" not in exc_info.value.message

    # ---- tool_invoke_log (同步版) ----

    def test_sync_preserves_custom_value_exception_code(self):

        expected_code = StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code

        @tool_invoke_log
        def failing_runtime_call():
            raise CustomValueException(
                error_code=expected_code,
                message="runtime api url is not allowed (private ip): '10.0.0.1'",
            )

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"):
            with pytest.raises(CustomValueException) as exc_info:
                failing_runtime_call()

        assert exc_info.value.error_code == expected_code
        assert "private ip" in exc_info.value.message

    def test_sync_wraps_unknown_exception_as_tool_exec_error(self):

        @tool_invoke_log
        def failing_runtime_call():
            raise ValueError("boom")

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"):
            with pytest.raises(CustomValueException) as exc_info:
                failing_runtime_call()

        assert exc_info.value.error_code == StatusCode.TOOL_EXEC_ERROR.code
        assert "boom" in exc_info.value.message

    # ---- get_logged_tool ----

    def test_get_logged_tool_run_preserves_custom_value_exception(self):
        """_run 应原样上抛 CustomValueException，不覆盖为 TOOL_LOG_ERROR (211303)。"""

        expected_code = StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code

        class FailingTool:
            def _run(self, *args, **kwargs):
                raise CustomValueException(
                    error_code=expected_code,
                    message="runtime api response JSON exceeds max depth 20",
                )

        LoggedTool = get_logged_tool(FailingTool)
        tool = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"):
            with pytest.raises(CustomValueException) as exc_info:
                tool._run("arg1")

        assert exc_info.value.error_code == expected_code
        assert "max depth" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_get_logged_tool_arun_preserves_custom_value_exception(self):
        """_arun 应原样上抛 CustomValueException，不覆盖为 TOOL_LOG_ERROR (211303)。"""

        expected_code = StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code

        class AsyncFailingTool:
            async def _arun(self, *args, **kwargs):
                raise CustomValueException(
                    error_code=expected_code,
                    message="runtime api response JSON object exceeds max item count 1000",
                )

        LoggedTool = get_logged_tool(AsyncFailingTool)
        tool = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"):
            with pytest.raises(CustomValueException) as exc_info:
                await tool._arun("arg1")

        assert exc_info.value.error_code == expected_code
        assert "max item count" in exc_info.value.message

    def test_get_logged_tool_run_wraps_unknown_exception_as_tool_log_error(self):
        """_run 对未知异常仍包装为 TOOL_LOG_ERROR (211303)，保留旧契约。"""

        class FailingTool:
            def _run(self, *args, **kwargs):
                raise RuntimeError("unexpected")

        LoggedTool = get_logged_tool(FailingTool)
        tool = LoggedTool()

        with patch(f"{MODULE_PATH}.time.time"), \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive", return_value=False), \
                patch(f"{MODULE_PATH}.logger"):
            with pytest.raises(CustomValueException) as exc_info:
                tool._run("arg1")

        assert exc_info.value.error_code == StatusCode.TOOL_LOG_ERROR.code
        assert "unexpected" in exc_info.value.message
        assert exc_info.value.__cause__ is not None
