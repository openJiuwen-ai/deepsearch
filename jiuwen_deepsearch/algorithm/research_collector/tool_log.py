# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import time
from typing import TypeVar, Any, Type

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.log_utils.log_metrics import metrics_logger, TIME_LOGGER_TAG
from jiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_sensitive_key(key):
    """判断键名是否包含敏感子字符串"""
    key_lower = key.lower()
    sensitive_keys = {"key", "url", "token"}
    return any(sub in key_lower for sub in sensitive_keys)


def get_logged_tool(base_tool_class: Type[T]) -> Type[T]:
    """
    Factory function that gets a logged version of any tool class.

    Args:
         base_tool_class: The original tool class to enhance with logging

    Returns:
        A new class that inherits both base tool's functionality and logging capabilities
    """
    # get metaclass of the base class
    base_metaclass = type(base_tool_class)

    # create a compatible metaclass that inherits from the base metaclass
    class LoggedToolMeta(base_metaclass):
        pass

    # create the logging mixin with the compatible metaclass
    class ToolLoggingMixin(metaclass=LoggedToolMeta):
        """Mixin class that adds logging capabilities to tools."""

        @staticmethod
        def _format_params(args: tuple, kwargs: dict) -> str:
            """Format arguments and keyword arguments into a readable string for logging."""
            args_str = []
            kwargs_str = []
            for arg in args:
                if not is_sensitive_key(arg):
                    args_str.append(repr(arg))
            for k, v in kwargs.items():
                if not is_sensitive_key(k):
                    kwargs_str.append(f"{k}={v!r}")
            return ", ".join(args_str + kwargs_str)

        @staticmethod
        def _truncate_result(result: Any) -> str:
            """Truncate long results to avoid overly verbose logs."""
            result_str = repr(result)
            return result_str[:100] + "..." if len(result_str) > 100 else result_str

        def _log_start(self, method: str, *args: Any, **kwargs: Any) -> None:
            """Log the start of tool execution with input parameters."""
            tool_name = self._get_tool_name()
            params = self._format_params(args, kwargs)
            if LogManager.is_sensitive():
                logger.info(f"[TOOL START] {tool_name}.{method}")
            else:
                logger.info(f"[TOOL START] {tool_name}.{method} | Params: {params}")

        def _log_end(self, method: str, result: Any, duration: float) -> None:
            """Log the successful completion of tool execution with results and duration"""
            tool_name = self._get_tool_name()
            result_summary = self._truncate_result(result)
            if LogManager.is_sensitive():
                logger.info(f"[TOOL END] {tool_name}.{method} | Duration: {duration: .2f}s")
            else:
                logger.info(f"[TOOL END] {tool_name}.{method} | Result: {result_summary} | Duration: {duration: .2f}s")

        def _log_error(self, method: str, error: Exception) -> None:
            """Log exceptions that occur during tool execution."""
            tool_name = self._get_tool_name()
            if LogManager.is_sensitive():
                logger.error(f"[TOOL ERROR] {tool_name}.{method}")
            else:
                logger.error(f"[TOOL ERROR] {tool_name}.{method} | Error: {str(error)}", exc_info=True)

        def _get_tool_name(self) -> str:
            """Extract the original tool name by removing logging-related suffixes."""
            return self.__class__.__name__.replace("WithLogging", "")

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            """Synchronized tool execution with logging and timing."""
            start_time = time.time()
            self._log_start("_run", *args, **kwargs)
            try:
                result = super()._run(*args, **kwargs)
            except Exception as e:
                self._log_error("_run", e)
                if LogManager.is_sensitive():
                    raise CustomValueException(
                        error_code=StatusCode.TOOL_LOG_ERROR.code,
                        message=StatusCode.TOOL_LOG_ERROR.errmsg) from e
                raise CustomValueException(
                    error_code=StatusCode.TOOL_LOG_ERROR.code,
                    message=StatusCode.TOOL_LOG_ERROR.errmsg.format(e=str(e))) from e
            self._log_end("_run", result, time.time() - start_time)
            return result

        async def _arun(self, *args: Any, **kwargs: Any) -> Any:
            """Asynchronous tool execution with logging and timing."""
            start_time = time.time()
            self._log_start("_arun", *args, **kwargs)
            try:
                result = await super()._arun(*args, **kwargs)
            except Exception as e:
                self._log_error("_arun", e)
                if LogManager.is_sensitive():
                    raise CustomValueException(
                        error_code=StatusCode.TOOL_LOG_ERROR.code,
                        message=StatusCode.TOOL_LOG_ERROR.errmsg) from e
                raise CustomValueException(
                    error_code=StatusCode.TOOL_LOG_ERROR.code,
                    message=StatusCode.TOOL_LOG_ERROR.errmsg.format(e=str(e))) from e
            self._log_end("_arun", result, time.time() - start_time)
            return result

    # create the final enhanced tool class
    class ToolWithLogging(ToolLoggingMixin, base_tool_class):
        pass

    # set a descriptive name for the enhanced class
    ToolWithLogging.__name__ = f"{base_tool_class.__name__}WithLogging"
    return ToolWithLogging


def tool_invoke_log(func):
    """
    A decorator that logs the input parameters and return results of a function,
    with enhanced exception handling capabilities.
    """

    def wrapper(*args, **kwargs):
        # extract function name for logging
        function_name = func.__name__

        # format positional and keyword arguments for logging
        formatted_args = []
        for arg in args:
            if not is_sensitive_key(arg):
                formatted_args.append(str(arg))
        for k, v in kwargs.items():
            if not is_sensitive_key(k):
                formatted_args.append(f"{k}={v}")
        args_text = ", ".join(formatted_args)

        # log function invocation with parameters
        start_time = time.time()
        if LogManager.is_sensitive():
            logger.info(f"[TOOL START] {function_name}")
        else:
            logger.info(f"[TOOL START] {function_name} | Args: {args_text}")

        try:
            # execute the original function
            result = func(*args, **kwargs)
        except Exception as e:
            # log exceptions with stack trace
            error_msg = f"[TOOL ERROR] {function_name} | Exception: {repr(e)}"
            if LogManager.is_sensitive():
                logger.error(f"[TOOL ERROR] {function_name} | Raise exception")
                raise CustomValueException(
                    error_code=StatusCode.TOOL_EXEC_ERROR.code,
                    message=StatusCode.TOOL_EXEC_ERROR.errmsg) from e
            logger.error(error_msg, exc_info=True)
            raise CustomValueException(
                    error_code=StatusCode.TOOL_EXEC_ERROR.code,
                    message=StatusCode.TOOL_EXEC_ERROR.errmsg.format(e=error_msg)) from e

        # log the return value
        duration = time.time() - start_time
        result_str = repr(result)
        log_str = result_str[:100] + "..." if len(result_str) > 100 else result_str
        if LogManager.is_sensitive():
            logger.info(f"[TOOL END] {function_name} | Duration: {duration: .2f}s")
        else:
            logger.info(f"[TOOL END] {function_name} | Result: {log_str} | Duration: {duration: .2f}s")

        return result

    return wrapper


def tool_invoke_log_async(func):
    """
    A decorator that logs the input parameters and return results of a function,
    with enhanced exception handling capabilities.
    """

    async def wrapper(*args, **kwargs):
        # extract function name for logging
        function_name = func.__name__
        stats_info_search = Config().service_config.stats_info_search

        # format positional and keyword arguments for logging
        formatted_args = []
        for arg in args:
            if not is_sensitive_key(arg):
                formatted_args.append(str(arg))
        for k, v in kwargs.items():
            if not is_sensitive_key(k):
                formatted_args.append(f"{k}={v}")
        args_text = ", ".join(formatted_args)

        # log function invocation with parameters
        start_time = time.time()
        if LogManager.is_sensitive():
            logger.info(f"[TOOL START] {function_name}")
        else:
            logger.info(f"[TOOL START] {function_name} | Args: {args_text}")

        try:
            # execute the original function asynchronously
            result = await func(*args, **kwargs)
        except Exception as e:
            # log exceptions with stack trace
            error_msg = f"[TOOL ERROR] {function_name} | Args: {args_text} | Exception: {repr(e)}"
            if LogManager.is_sensitive():
                logger.error(f"[TOOL ERROR] {function_name} | Raise exception")
                raise CustomValueException(
                    error_code=StatusCode.TOOL_EXEC_ERROR.code,
                    message=StatusCode.TOOL_EXEC_ERROR.errmsg.format) from e
            logger.error(error_msg, exc_info=True)
            raise CustomValueException(
                error_code=StatusCode.TOOL_EXEC_ERROR.code,
                message=StatusCode.TOOL_EXEC_ERROR.errmsg.format(e=error_msg)) from e

        # log the return value
        duration = time.time() - start_time
        result_str = repr(result)
        log_str = result_str[:100] + "..." if len(result_str) > 100 else result_str
        if LogManager.is_sensitive():
            logger.info(f"[TOOL END] {function_name} | Tool result count: {len(result)} | Duration: {duration: .2f}s")
        else:
            logger.info(f"[TOOL END] {function_name} | Args: {args_text} | Tool result count: {len(result)} | "
                        f"Result content: {log_str} | Duration: {duration: .2f}s")

        if stats_info_search:
            search_stat = {"function_name": function_name, "duration": duration}

            if kwargs.get("search_engine_name"):
                search_stat["search_engine"] = kwargs.get("search_engine_name")
            if kwargs.get("query"):
                search_stat["query"] = kwargs.get("query")

            if result and result.get("search_results") and isinstance(result.get("search_results"), list):
                res_len_list = []
                for search_result in result.get("search_results"):
                    res_len_list.append(len(search_result.get('content', '')))
                search_stat["res_count"] = len(res_len_list)
                search_stat["res_len_list"] = res_len_list

            metrics_logger.info(
                f"{TIME_LOGGER_TAG} thread_id: {session_id_ctx.get()} ------ [SEARCH TOOL STATISTICS]: {search_stat}"
            )
        return result

    return wrapper
