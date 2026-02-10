# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.param import Param

from jiuwen_deepsearch.algorithm.research_collector.tool_log import tool_invoke_log_async
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.utils.constants_utils.search_engine_constants import LocalSearch
from jiuwen_deepsearch.framework.jiuwen.tools.Search_API import (
    LocalDatasetAPIWrapper,
    NativeLocalSearchAPIWrapper,
    load_external_search_tools,
)
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import local_search_context
from jiuwen_deepsearch.common.exception import CustomValueException

logger = logging.getLogger(__name__)

local_search_mapping = {
    LocalSearch.OPENAPI.value: LocalDatasetAPIWrapper,
    LocalSearch.NATIVE.value: NativeLocalSearchAPIWrapper,
}


def update_local_search_mapping(func_path: str, func_name: str):
    """加载外部搜索工具，并更新本地搜索映射字典"""
    engine_name, external_mapping = load_external_search_tools(func_path, func_name)
    if external_mapping:
        local_search_mapping["custom"] = external_mapping[engine_name]
    return local_search_mapping


@tool_invoke_log_async
async def run_local_search(query: str, search_engine_name: str):
    """运行本地搜索"""
    api_wrapper = local_search_context.get().get(search_engine_name)
    if not api_wrapper:
        raise CustomValueException(
            StatusCode.LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR.code,
            StatusCode.LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR.errmsg.format(name=search_engine_name)
        )
    try:
        result = await api_wrapper.aresults(query)
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f"Error when run local search")
        else:
            logger.exception(f"Error when run local search: {e}")
        return dict(search_engine=search_engine_name, search_results=[repr(e)])
    return dict(search_engine=search_engine_name, search_results=result)


def create_local_search_tool():
    """获取本地搜索工具"""
    local_search_tool = LocalFunction(
        name="local_search_tool",
        description="Use local search engine to get local dataset information.",
        params=[
            Param(name="query",
                  description="Search query of current step.",
                  param_type="String",
                  required=True)
        ],
        func=run_local_search
    )
    return local_search_tool
