# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from jiuwen_deepsearch.algorithm.research_collector.tool_log import tool_invoke_log_async
from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.framework.jiuwen.tools.Search_API import (
    XunfeiSearchAPIWrapper,
    TavilySearchAPIWrapper,
    GoogleSearchAPIWrapper,
    PetalSearchAPIWrapper,
    load_external_search_tools
)
from jiuwen_deepsearch.utils.constants_utils.session_contextvars import web_search_context
from jiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

search_engine_mapping = {
    SearchEngine.TAVILY.value: TavilySearchAPIWrapper,
    SearchEngine.GOOGLE.value: GoogleSearchAPIWrapper,
    SearchEngine.XUNFEI.value: XunfeiSearchAPIWrapper,
    SearchEngine.PETAL.value: PetalSearchAPIWrapper,
}


def update_web_search_mapping(func_path: str, func_name: str):
    """加载外部搜索工具，并更新本地搜索映射字典"""
    engine_name, external_mapping = load_external_search_tools(func_path, func_name)
    if external_mapping:
        search_engine_mapping["custom"] = external_mapping[engine_name]
    return search_engine_mapping


@tool_invoke_log_async
async def run_web_search(query: str, search_engine_name: str):
    """运行网页搜索"""
    api_wrapper = web_search_context.get().get(search_engine_name)
    if not api_wrapper:
        raise CustomValueException(
            StatusCode.WEB_SEARCH_INSTANCE_OBTAIN_ERROR.code,
            StatusCode.WEB_SEARCH_INSTANCE_OBTAIN_ERROR.errmsg.format(name=search_engine_name),
        )
    try:
        result = await api_wrapper.aresults(query)
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f"Error when run web search {search_engine_name}")
        else:
            logger.exception(f"Error when run web search {search_engine_name}: {e}")
        return dict(search_engine=search_engine_name, search_results=[repr(e)])
    return dict(search_engine=search_engine_name, search_results=result)


def create_web_search_tool():
    """获取网页搜索工具"""

    card = ToolCard(
        id="web_search_tool",
        name="web_search_tool",
        description="Use web search engine to get web information.",
        input_params={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query of current step."
                },
                "search_engine_name": {
                    "type": "string",
                    "description": "Name of the search engine to use."
                }
            },
            "required": ["query", "search_engine_name"]
        }
    )
    web_search_tool = LocalFunction(
        card=card,
        func=run_web_search
    )
    return web_search_tool
