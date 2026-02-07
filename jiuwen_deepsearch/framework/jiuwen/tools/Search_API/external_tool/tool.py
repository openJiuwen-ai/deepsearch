# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import importlib

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def load_external_search_tools(func_path: str, func_name: str):
    """Load external search tools and return tool names and mappings"""
    engine_name = ""
    external_mapping = {}

    if func_path and func_name:
        external_config = {
            "external_search": func_path,
            "func_name": func_name
        }
    else:
        logger.info(f"[load_external_search_tools] External tool configuration not found, internal tool only")
        return engine_name, external_mapping

    for key, value in external_config.items():
        if not isinstance(value, str):
            logger.error(f"[load_external_search_tools] External tool configuration is not str: {key}, "
                         f"internal tool only")
            return engine_name, external_mapping

    for key, value in external_config.items():
        if key == "func_name":
            continue
        try:
            func_name = external_config.get("func_name", "")
            engine_name = key
            func_full_path = value
            spec = importlib.util.spec_from_file_location(name=func_name, location=func_full_path)
            if spec is None:
                raise CustomValueException(
                    error_code=StatusCode.LOAD_EXTEND_TOOLS_FAILED.code,
                    message=StatusCode.LOAD_EXTEND_TOOLS_FAILED.errmsg)
            plugin_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin_module)
            if plugin_module and hasattr(plugin_module, func_name):
                external_mapping[engine_name] = getattr(plugin_module, func_name)
                logger.info(f"[load_external_search_tools] Successfully loaded the external")
            else:
                logger.info(f"[load_external_search_tools] Load external search tool failed, "
                            f"function: {func_name} not found, internal tools only")
                engine_name, external_mapping = "", {}
                return engine_name, external_mapping
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(f"[load_external_search_tools] Failed to load the external tool: {engine_name}")
            else:
                logger.error(f"[load_external_search_tools] Failed to load the external tool: {engine_name}, "
                             f"Error: {str(e)}")
            engine_name, external_mapping = "", {}
            return engine_name, external_mapping

        return engine_name, external_mapping
