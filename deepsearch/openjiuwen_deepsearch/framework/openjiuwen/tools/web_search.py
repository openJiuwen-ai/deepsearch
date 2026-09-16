# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from datetime import date, timedelta

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from openjiuwen_deepsearch.algorithm.research_collector.tool_log import tool_invoke_log_async
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import TemporalScope
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api import (
    XunfeiSearchAPIWrapper,
    TavilySearchAPIWrapper,
    PubMedSearchAPIWrapper,
    ArxivSearchAPIWrapper,
    SemanticScholarSearchAPIWrapper,
    GoogleSearchAPIWrapper,
    PetalSearchAPIWrapper,
    BochaSearchAPIWrapper,
    JinaSearchAPIWrapper,
    PerplexitySearchAPIWrapper,
    load_external_search_tools
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    RETRYABLE_HTTP_STATUSES,
    http_status_code,
    is_transient_connection_error,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import web_search_context
from openjiuwen_deepsearch.utils.constants_utils.scholarly_constants import SCHOLARLY_PROVIDER_NAMES
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import (
    SearchEngine,
    TEMPORAL_SCOPE_SEARCH_ENGINES,
)
from openjiuwen_deepsearch.utils.common_utils.url_utils import normalize_domains
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.rate_limiter_utils.qps_limiter import qps_rate_limit_async

logger = logging.getLogger(__name__)

search_engine_mapping = {
    SearchEngine.TAVILY.value: TavilySearchAPIWrapper,
    SearchEngine.PUBMED.value: PubMedSearchAPIWrapper,
    SearchEngine.ARXIV.value: ArxivSearchAPIWrapper,
    SearchEngine.SEMANTIC_SCHOLAR.value: SemanticScholarSearchAPIWrapper,
    SearchEngine.GOOGLE.value: GoogleSearchAPIWrapper,
    SearchEngine.XUNFEI.value: XunfeiSearchAPIWrapper,
    SearchEngine.PETAL.value: PetalSearchAPIWrapper,
    SearchEngine.BOCHA.value: BochaSearchAPIWrapper,
    SearchEngine.JINA.value: JinaSearchAPIWrapper,
    SearchEngine.PERPLEXITY.value: PerplexitySearchAPIWrapper,
    SearchEngine.SERPER.value: GoogleSearchAPIWrapper,
}


SITE_DOMAIN_CONSTRAINT_SEARCH_ENGINES = {
    SearchEngine.TAVILY.value,
}


def get_web_search_api_wrapper(search_engine_name: str | None = None):
    """Resolve a registered web search wrapper from the current session context."""
    try:
        web_search_engines = web_search_context.get() or {}
    except LookupError:
        web_search_engines = {}

    if search_engine_name:
        return search_engine_name, web_search_engines.get(search_engine_name)

    if not web_search_engines:
        return "", None

    if len(web_search_engines) > 1:
        engine_names = list(web_search_engines.keys())
        logger.warning(
            "Multiple web search engines are registered in context; defaulting to the first entry: %s",
            engine_names,
        )

    resolved_name, api_wrapper = next(iter(web_search_engines.items()))
    return resolved_name, api_wrapper


def apply_web_search_domain_constraints(
        search_engine_name: str,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
) -> bool:
    """
    将意图识别出的域名约束合并到已初始化的搜索引擎实例.

    仅修改原生支持 include_domains/exclude_domains 的搜索引擎实例。
    实例中的现有值来自初始化配置，因此这里会保留配置域名，并追加 query 中识别出的域名。
    """
    if search_engine_name not in SITE_DOMAIN_CONSTRAINT_SEARCH_ENGINES:
        return False

    try:
        web_search_engines = web_search_context.get() or {}
    except LookupError:
        web_search_engines = {}
    api_wrapper = web_search_engines.get(search_engine_name)
    if not api_wrapper:
        return False

    if search_engine_name == "tavily":
        merged_include = normalize_domains(getattr(api_wrapper, "include_domains", None))
        merged_include.extend(normalize_domains(include_domains))
        api_wrapper.include_domains = normalize_domains(merged_include)

        merged_exclude = normalize_domains(getattr(api_wrapper, "exclude_domains", None))
        merged_exclude.extend(normalize_domains(exclude_domains))
        api_wrapper.exclude_domains = normalize_domains(merged_exclude)

        logger.info(
            "apply_web_search_domain_constraints [%s]: intent include_domains=%s, "
            "exclude_domains=%s; merged include_domains=%s, exclude_domains=%s",
            search_engine_name,
            include_domains,
            exclude_domains,
            api_wrapper.include_domains,
            api_wrapper.exclude_domains,
        )

    return True


def apply_web_search_temporal_scope(
        search_engine_name: str,
        temporal_scope: TemporalScope | dict | None,
) -> bool:
    """为当前会话的 Tavily 实例设置来源发表时间检索边界。

    Tavily 的日期过滤混合使用发表时间与最后更新时间，结果仍由 collector 按统一发表日期
    后置过滤。内部边界为包含关系，而 Tavily 使用严格 ``after``/``before``，所以开始边界
    向前、结束边界向后各移动一天。内容时间不会发送原生日期参数。

    Args:
        search_engine_name: 当前配置的 web 搜索引擎名称。
        temporal_scope: 结构化时间范围；可传模型、兼容字典或空值。

    Returns:
        True 表示找到了支持该能力的当前会话 wrapper 并完成设置，否则返回 False。
    """
    if search_engine_name not in TEMPORAL_SCOPE_SEARCH_ENGINES:
        return False

    try:
        web_search_engines = web_search_context.get() or {}
    except LookupError:
        web_search_engines = {}
    api_wrapper = web_search_engines.get(search_engine_name)
    if not api_wrapper:
        return False

    scope = None
    if temporal_scope is not None:
        try:
            scope = temporal_scope if isinstance(temporal_scope, TemporalScope) else TemporalScope.model_validate(
                temporal_scope
            )
        except (TypeError, ValueError):
            scope = None

    api_wrapper.start_date = None
    api_wrapper.end_date = None
    if scope and scope.constraint_type == "source_date":
        if scope.start_date and scope.start_date > date.min:
            api_wrapper.start_date = (scope.start_date - timedelta(days=1)).isoformat()
        if scope.end_date and scope.end_date < date.max:
            api_wrapper.end_date = (scope.end_date + timedelta(days=1)).isoformat()

    logger.info(
        "apply_web_search_temporal_scope [%s]: constraint_type=%s, native start_date=%s, end_date=%s",
        search_engine_name,
        scope.constraint_type if scope else "",
        api_wrapper.start_date,
        api_wrapper.end_date,
    )
    return True


def update_web_search_mapping(func_path: str, func_name: str):
    """加载外部搜索工具，并更新本地搜索映射字典"""
    engine_name, external_mapping = load_external_search_tools(func_path, func_name)
    if external_mapping:
        search_engine_mapping["custom"] = external_mapping[engine_name]
    return search_engine_mapping


@tool_invoke_log_async
@qps_rate_limit_async
async def run_web_search(query: str, search_engine_name: str):
    """运行网页搜索"""
    resolved_name, api_wrapper = get_web_search_api_wrapper(search_engine_name)
    if not api_wrapper:
        raise CustomValueException(
            StatusCode.WEB_SEARCH_INSTANCE_OBTAIN_ERROR.code,
            StatusCode.WEB_SEARCH_INSTANCE_OBTAIN_ERROR.errmsg.format(name=search_engine_name),
        )
    try:
        result = await api_wrapper.aresults(query)
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f"Error when run web search {resolved_name}")
        else:
            logger.exception(f"Error when run web search {resolved_name}: {e}")
        retryable = resolved_name not in SCHOLARLY_PROVIDER_NAMES and (
            is_transient_connection_error(e)
            or http_status_code(e) in RETRYABLE_HTTP_STATUSES
        )
        return dict(search_engine=resolved_name,
                    search_results=[],
                    error=f"Error when run web search {resolved_name}: {e}",
                    retryable=retryable)
    return dict(search_engine=resolved_name, search_results=result)


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
