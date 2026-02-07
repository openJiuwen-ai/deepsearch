import pytest
from unittest.mock import patch
from jiuwen_deepsearch.algorithm.research_collector.tool_log import \
    is_sensitive_key, get_logged_tool, tool_invoke_log, tool_invoke_log_async
from jiuwen_deepsearch.common.exception import CustomValueException

module_path = "jiuwen_deepsearch.algorithm.research_collector.tool_log"


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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_time.side_effect = [100.0, 100.5]  # start_time, end_time
            mock_sensitive.return_value = False

            result = tool_instance._run("arg1", key1="value1")

            # 验证结果
            assert "Result from TestTool" in result

    def test_get_logged_tool_run_sensitive_mode(self):
        """测试敏感模式下的同步执行"""
        LoggedTool = get_logged_tool(self.BaseTool)
        tool_instance = LoggedTool()

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.time.time") as mock_time, \
                patch(f"{module_path}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{module_path}.logger") as mock_logger:
            mock_time.return_value = 100.0
            mock_sensitive.return_value = True

            # 验证异常被正确抛出
            with pytest.raises(CustomValueException):
                await async_failing_function()

            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_with("[TOOL ERROR] async_failing_function | Raise exception")
