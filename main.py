import argparse
import asyncio
import base64
import copy
import json
import logging
import os
import uuid
from pathlib import Path

from openjiuwen_deepsearch.algorithm.search_nodes.utils import ensure_api_keys_bytearray
from openjiuwen_deepsearch.config.config import Config, LLMConfig
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
    parse_endnode_content,
)
from openjiuwen_deepsearch.utils.debug_utils.result_exporter import ResultExporter
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.run_telemetry import (
    RunTelemetryConfig,
    emit,
    run_telemetry_session,
)
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from openjiuwen_deepsearch.utils.question_model_router import route_question_search_path

LogManager.init(
    log_dir="./output/logs",
    max_bytes=100 * 1024 * 1024,
    backup_count=20,
    level="INFO",
    is_sensitive=False,
)

ResultExporter.init(results_dir="./output/results")

logger = logging.getLogger(__name__)

RESEARCH_ENGINES_ALLOWING_EMPTY_SEARCH_URL = {
    "tavily",
    "google",
    "bocha",
    "jina",
    "perplexity",
    "serper",
}


async def run_jiuwen_workflow(query: str, agent_config: dict, report_template: str):
    """
    Run the openJiuwen-DeepSearch workflow with the given query and agent configuration.

    Args:
        query (str): The input query string.
        agent_config (dict): Configuration for the agent.
        report_template (str): The report template.

    Returns:
        None
    """
    conversation_id = str(uuid.uuid4())
    _qp = (
        "***"
        if LogManager.is_sensitive()
        else ((query[:200] + "…") if len(query) > 200 else query)
    )
    emit(
        "run_started",
        {
            "conversation_id": conversation_id,
            "search_mode": agent_config.get("search_mode"),
            "query_preview": _qp,
        },
        source="main.run_jiuwen_workflow",
        action_id=None,
    )
    if agent_config.get("enable_question_router") and agent_config.get("search_mode") == "search":
        general_raw = copy.deepcopy((agent_config.get("llm_config") or {}).get("general") or {})
        general_llm = LLMConfig.model_validate(general_raw)
        ext = general_llm.extension or {}
        raw_extra = ext.get("extra_body")
        extra_body = dict(raw_extra) if isinstance(raw_extra, dict) else None
        llm_entry = create_llm_obj(general_llm)
        label = await route_question_search_path(query, llm_entry, extra_body=extra_body)
        if label == 0:
            agent_config["search_mode"] = "react"
            emit(
                "question_router_routed_to_react",
                {
                    "conversation_id": conversation_id,
                    "router_label": label,
                    "search_mode_before": "search",
                    "search_mode_after": "react",
                    "query_preview": _qp,
                },
                source="main.run_jiuwen_workflow.question_router",
                action_id=None,
            )
        _qp = (
            "***"
            if LogManager.is_sensitive()
            else ((query[:200] + "…") if len(query) > 200 else query)
        )
        logger.info(
            "question_router: label=%s -> %s (0=ReAct, 1=DeepSearch) | %s",
            label,
            agent_config.get("search_mode"),
            _qp,
        )

    agent_factory = AgentFactory()
    agent = agent_factory.create_agent(agent_config)
    async for chunk in agent.run(
        message=query,
        conversation_id=conversation_id,
        report_template=report_template,
        interrupt_feedback="",
        agent_config=agent_config,
    ):
        logger.debug("[Stream message from node: %s]", chunk)
        chunk_content = json.loads(chunk)
        report_result = parse_endnode_content(chunk_content)
        if report_result:
            logger.debug("[Final Report is: %s]", report_result)


def read_file_safely(file_name: str) -> bytes:
    """
    读取文件，确保文本文件不会出现GBK解码错误。
    - 二进制文件（pdf/docx）rb读取
    - 文本文件（md/txt） utf-8读取
    """
    if not file_name.lower().endswith(".md"):
        with open(file_name, "rb") as f:
            return f.read()
    else:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read().encode("utf-8")


async def generate_template_and_run(file_name: str, is_template: bool, mode: str, query: str, agent_config: dict):
    """
    根据输入文件生成报告模板，并运行Jiuwen工作流。

    Args:
        file_name (str): 样例报告or模板文件路径
        is_template (bool): 是否为模板文件
        mode (str): 模式，支持 "template" 和 "all", template仅生成模板，all先生成模板，再做research
        query (str): 用户输入query
        agent_config (dict): Configuration for the agent.

    Returns:
        None
    """
    agent_factory = AgentFactory()
    template_agent_config = copy.deepcopy(agent_config)
    agent = agent_factory.create_agent(template_agent_config)

    result = await agent.generate_template(
        file_name=file_name,
        file_stream=base64.b64encode(read_file_safely(file_name)).decode("utf-8"),
        is_template=is_template,
        agent_config=template_agent_config,
    )

    if result.get("status") == "success":
        path = Path(file_name)
        output_name = path.stem + ".md"
        save_path = "./saved_templates/"
        os.makedirs(save_path, exist_ok=True)
        output_path = os.path.join(save_path, output_name)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(base64.b64decode(result["template_content"]).decode("utf-8"))
        logger.info(f"模板已保存至:{output_path}")
    else:
        logger.error("模板生成失败")
        return

    if mode == "all":
        await run_jiuwen_workflow(query, copy.deepcopy(agent_config), result["template_content"])


def _missing_required_args(args: argparse.Namespace, arg_names: list[str]) -> list[str]:
    """返回值为空的 CLI 参数名列表。"""
    missing = []
    for arg_name in arg_names:
        value = getattr(args, arg_name)
        if value in ("", None):
            missing.append(f"--{arg_name}")
    return missing


def _missing_research_web_args(args: argparse.Namespace) -> list[str]:
    """Return missing research web args while respecting engine defaults."""
    required_args = ["web_search_engine_name", "web_search_api_key"]
    engine_name = (args.web_search_engine_name or "").strip().lower()
    if engine_name not in RESEARCH_ENGINES_ALLOWING_EMPTY_SEARCH_URL:
        required_args.append("web_search_url")
    return _missing_required_args(args, required_args)


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """根据 mode / search_mode 执行条件化参数校验。"""
    missing_llm_args = _missing_required_args(
        args,
        ["llm_model_name", "llm_model_type", "llm_base_url", "llm_api_key"],
    )
    if missing_llm_args:
        parser.error(f"必须提供以下 LLM 参数: {', '.join(missing_llm_args)}")

    if args.llm_ssl_verify and not args.llm_ssl_cert:
        parser.error("开启 --llm_ssl_verify 时必须提供 --llm_ssl_cert")

    if args.tool_ssl_verify and not args.tool_ssl_cert:
        parser.error("开启 --tool_ssl_verify 时必须提供 --tool_ssl_cert")

    if args.mode in ("template", "all") and args.search_mode != "research":
        parser.error("--mode template 和 --mode all 仅支持 --search_mode research")

    if args.mode in ("template", "all") and not args.file_path:
        parser.error("使用 --mode template 或 --mode all 时必须提供 --file_path")

    if args.mode == "all" and not args.query:
        parser.error("使用 --mode all 时必须提供 --query")

    if args.vlm_chart_generator_enable and args.search_mode != "research":
        parser.error("--vlm_chart_generator_enable 仅支持 --search_mode research")

    if args.mode in ("query", "all") and args.search_mode == "research":
        missing_research_web_args = _missing_research_web_args(args)
        if missing_research_web_args:
            parser.error(f"research 模式必须提供以下参数: {', '.join(missing_research_web_args)}")

    if args.mode == "query" and args.search_mode in ("search", "react"):
        if args.tool_map == "search_fetch":
            missing_search_args = _missing_required_args(
                args,
                [
                    "web_search_engine_name",
                    "web_search_api_key",
                    "fetch_provider_name",
                    "fetch_api_key",
                ],
            )
            if missing_search_args:
                parser.error(
                    "search / react 模式 (tool_map=search_fetch) 必须提供以下参数: "
                    + ", ".join(missing_search_args)
                )
        elif args.tool_map == "retrieve":
            missing_retrieve = _missing_required_args(
                args,
                [
                    "milvus_host",
                    "database_name",
                    "collection_name",
                    "embedder_model_name",
                    "embedder_api_key",
                    "embedder_base_url",
                ],
            )
            if missing_retrieve:
                parser.error(
                    "search / react 模式 (tool_map=retrieve) 必须提供以下参数: "
                    + ", ".join(missing_retrieve)
                )
            if args.milvus_port <= 0 or args.milvus_port > 65535:
                parser.error("--milvus_port 必须在 1–65535 范围内")


def main(
    argv: list[str] | None = None,
    telemetry: RunTelemetryConfig | None = None,
) -> int:
    """CLI entry. Optional ``telemetry`` enables HTTP run telemetry for this process.

    - ``argv is None``: parse ``sys.argv`` (normal ``python main.py``).
    - ``argv`` is a list: parse exactly those tokens (used by ``run_main_with_telemetry``).
    """
    with run_telemetry_session(telemetry):
        parser = argparse.ArgumentParser(description="Run deepsearch workflow")
        parser.add_argument("--query", nargs="*", default=["AI手机研究报告"], help="The query to process")
        parser.add_argument(
            "--mode",
            choices=["query", "template", "all"],
            default="query",
            help="Operation mode: query, template or all",
        )
        parser.add_argument(
            "--search_mode",
            choices=["research", "search", "react"],
            default="research",
            help="research: Deepresearch. search: DeepSearch graph. react: simple ReAct + same tools as search.",
        )

        llm_group = parser.add_argument_group("LLM", "llm 配置参数")
        llm_group.add_argument("--llm_model_name", type=str, default="", help="llm 模型名称")
        llm_group.add_argument(
            "--llm_model_type",
            type=str,
            default="",
            help="llm 模型类型，openai or siliconflow",
        )
        llm_group.add_argument("--llm_base_url", type=str, default="", help="llm 模型服务地址")
        llm_group.add_argument("--llm_api_key", type=str, default="", help="llm 模型密钥")
        llm_group.add_argument("--llm_ssl_verify", action="store_true", help="开启 LLM SSL 校验")
        llm_group.add_argument("--llm_ssl_cert", type=str, default="", help="LLM SSL 证书")

        research_group = parser.add_argument_group("Research", "研究 / 报告模板与可选 VLM 图表")
        research_group.add_argument(
            "--execution_method",
            choices=["parallel", "dependency_driving", "hybrid"],
            default="parallel",
            help="execution method of workflow",
        )
        research_group.add_argument(
            "--web_search_engine_name",
            type=str,
            default="",
            help="联网增强引擎名称, tavily or google",
        )
        research_group.add_argument("--web_search_api_key", type=str, default="", help="联网增强引擎密钥")
        research_group.add_argument("--web_search_url", type=str, default="", help="联网增强引擎服务地址")
        research_group.add_argument(
            "--max_web_search_results",
            type=int,
            default=5,
            help="联网增强搜索单次请求返回结果数量",
        )
        research_group.add_argument(
            "--report_template",
            type=str,
            default="",
            help="Base64 encoded report template content for research mode(optional)",
        )
        research_group.add_argument("--file_path", type=str, default="", help="样例报告or模板文件路径")
        research_group.add_argument(
            "--is_template",
            action="store_true",
            help="Indicates whether the input file is a template",
        )
        research_group.add_argument("--vlm_model_name", type=str, help="vlm 模型名称")
        research_group.add_argument("--vlm_model_type", type=str, help="vlm 模型类型，openai or siliconflow")
        research_group.add_argument("--vlm_base_url", type=str, help="vlm 模型服务地址")
        research_group.add_argument("--vlm_api_key", type=str, help="vlm 模型密钥")
        research_group.add_argument(
            "--vlm_chart_generator_max_iterations",
            type=int,
            default=1,
            help="vlm 迭代生成图最大迭代次数, 最大值: 3",
        )
        research_group.add_argument("--vlm_chart_generator_enable", action="store_true", help="开启 vlm 迭代生成图")

        search_group = parser.add_argument_group("Search", "搜索引擎 / 向量库（DeepSearch 等）")
        search_group.add_argument(
            "--tool_map",
            choices=["search_fetch", "retrieve"],
            default="search_fetch",
            help="search tool map",
        )
        search_group.add_argument("--max_workers", type=int, default=5, help="并发执行 action 的最大协程数")
        search_group.add_argument(
            "--retry_count_on_empty_action_space",
            type=int,
            default=3,
            help="action pool 为空时重新 find_action 的最大次数",
        )
        search_group.add_argument("--time_limit", type=int, default=4800, help="单个问题最大运行时间（秒）")
        search_group.add_argument(
            "--actions_explored_limit",
            type=int,
            default=200,
            help="最大探索 action 数量，200=无限制",
        )
        search_group.add_argument("--fail_limit", type=int, default=0, help="最大连续失败次数，0=无限制")
        search_group.add_argument("--answer_mode_top_k", type=int, default=1, help="答案模式保留候选答案数量")
        search_group.add_argument("--provide_best_guess", action="store_true", help="超时时返回当前最佳猜测答案")
        search_group.add_argument(
            "--enable_question_router",
            action="store_true",
            default=False,
            help="With --search_mode search: call LLM router first (0→react, 1→DeepSearch)",
        )
        search_group.add_argument(
            "--fetch_provider_name",
            type=str,
            default="",
            help="网页抓取 provider 名称，例如 jina",
        )
        search_group.add_argument("--fetch_api_key", type=str, default="", help="网页抓取 provider 密钥")
        search_group.add_argument("--fetch_base_url", type=str, default="", help="网页抓取 provider 服务地址")
        search_group.add_argument("--milvus_host", type=str, default="localhost", help="milvus 主机地址")
        search_group.add_argument("--milvus_port", type=int, default=19530, help="milvus 端口")
        search_group.add_argument("--database_name", type=str, default="default", help="数据库名称")
        search_group.add_argument(
            "--collection_name",
            type=str,
            default="",
            help="集合名称",
        )
        search_group.add_argument(
            "--embedder_model_name",
            type=str,
            default="",
            help="embedder 模型名称",
        )
        search_group.add_argument("--embedder_api_key", type=str, default="", help="embedder 模型密钥")
        search_group.add_argument("--embedder_base_url", type=str, default="", help="embedder 模型服务地址")

        parser.add_argument("--tool_ssl_verify", action="store_true", help="开启 Tool SSL 校验")
        parser.add_argument("--tool_ssl_cert", type=str, default="", help="Tool SSL 证书")

        args = parser.parse_args(argv) if argv is not None else parser.parse_args()
        _validate_args(parser, args)
        query_list = args.query if isinstance(args.query, list) else [args.query]
        joined_query = " ".join(query_list)

        os.environ["LLM_SSL_VERIFY"] = "true" if args.llm_ssl_verify else "false"
        os.environ["LLM_SSL_CERT"] = args.llm_ssl_cert

        os.environ["TOOL_SSL_VERIFY"] = "true" if args.tool_ssl_verify else "false"
        os.environ["TOOL_SSL_CERT"] = args.tool_ssl_cert

        current_agent_config = Config().agent_config.model_dump()

        # 解析llm配置
        current_agent_config["llm_config"]["general"] = {}
        current_agent_config["llm_config"]["general"]["model_name"] = args.llm_model_name
        current_agent_config["llm_config"]["general"]["model_type"] = args.llm_model_type
        current_agent_config["llm_config"]["general"]["base_url"] = args.llm_base_url
        current_agent_config["llm_config"]["general"]["api_key"] = bytearray(args.llm_api_key, encoding="utf-8")
        current_agent_config["llm_config"]["general"]["hyper_parameters"] = {
            "top_p": 1.0,
            "temperature": 1.0,
        }

        # 解析多模态llm配置
        if args.vlm_chart_generator_enable:
            # vlm迭代轮次大于0, 必须传入vlm模型相关配置
            current_agent_config["vlm_chart_generator_enable"] = True
            current_agent_config["vlm_chart_generator_max_iterations"] = args.vlm_chart_generator_max_iterations
            if args.vlm_chart_generator_max_iterations > 0:
                vlm_configs = [
                    args.vlm_model_name,
                    args.vlm_model_type,
                    args.vlm_base_url,
                    args.vlm_api_key,
                ]
                if all(vlm_configs):
                    current_agent_config["llm_config"]["vlm_chart_generating"] = {}
                    current_agent_config["llm_config"]["vlm_chart_generating"]["model_name"] = args.vlm_model_name
                    current_agent_config["llm_config"]["vlm_chart_generating"]["model_type"] = args.vlm_model_type
                    current_agent_config["llm_config"]["vlm_chart_generating"]["base_url"] = args.vlm_base_url
                    current_agent_config["llm_config"]["vlm_chart_generating"]["api_key"] = bytearray(
                        args.vlm_api_key, encoding="utf-8"
                    )
                else:
                    # current_agent_config["vlm_chart_generator_enable"] = False
                    # current_agent_config["vlm_chart_generator_max_iterations"] = 0
                    logger.warning("开启vlm迭代生成图开关且vlm迭代轮次大于0时，\
                                   需提供 vlm_model_name、type、base_url 和 api_key，将尝试用llm进行vlm迭代优化。")

        # 解析联网增强引擎配置。search_fetch 与 research 共用同一搜索引擎配置契约。
        if args.search_mode == "research" or args.tool_map == "search_fetch":
            current_agent_config["web_search_engine_config"]["search_engine_name"] = args.web_search_engine_name
            current_agent_config["web_search_engine_config"]["search_api_key"] = bytearray(
                args.web_search_api_key,
                encoding="utf-8",
            )
            current_agent_config["web_search_engine_config"]["search_url"] = args.web_search_url
            current_agent_config["web_search_engine_config"]["max_web_search_results"] = args.max_web_search_results
        current_agent_config["search_workflow_per_question_params"]["tool_map"] = args.tool_map
        current_agent_config["search_workflow_per_question_params"]["max_workers"] = args.max_workers
        current_agent_config["search_workflow_per_question_params"][
            "retry_count_on_empty_action_space"
        ] = args.retry_count_on_empty_action_space
        current_agent_config["search_workflow_per_question_params"]["time_limit"] = args.time_limit
        current_agent_config["search_workflow_per_question_params"]["actions_explored_limit"] = (
            args.actions_explored_limit
        )
        current_agent_config["search_workflow_per_question_params"]["fail_limit"] = args.fail_limit
        current_agent_config["search_workflow_per_question_params"]["answer_mode_top_k"] = args.answer_mode_top_k
        current_agent_config["search_workflow_per_question_params"]["provide_best_guess"] = args.provide_best_guess

        current_agent_config["workflow_human_in_the_loop"] = False
        current_agent_config["outline_interaction_enabled"] = False
        current_agent_config["search_mode"] = args.search_mode
        current_agent_config["enable_question_router"] = args.enable_question_router
        if args.execution_method.strip() == ExecutionMethod.DEPENDENCY_DRIVING.value:
            current_agent_config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
        elif args.execution_method.strip() == ExecutionMethod.HYBRID.value:
            current_agent_config["execution_method"] = ExecutionMethod.HYBRID.value
        else:
            current_agent_config["execution_method"] = ExecutionMethod.PARALLEL.value

        if args.mode == "query":
            if not args.query or args.search_mode not in ("research", "search", "react"):
                parser.print_help()
            else:
                if args.search_mode in ("search", "react"):
                    current_agent_config["search_mode"] = args.search_mode
                    if args.tool_map == "search_fetch":
                        current_agent_config["web_fetch_provider_config"] = {
                            "provider_name": args.fetch_provider_name,
                            "api_key": bytearray(args.fetch_api_key, encoding="utf-8"),
                            "base_url": args.fetch_base_url,
                        }
                    elif args.tool_map == "retrieve":
                        current_agent_config["search_workflow_milvus_config"]["milvus_host"] = args.milvus_host
                        current_agent_config["search_workflow_milvus_config"]["milvus_port"] = args.milvus_port
                        current_agent_config["search_workflow_milvus_config"]["database_name"] = args.database_name
                        current_agent_config["search_workflow_milvus_config"]["collection_name"] = args.collection_name
                        current_agent_config["search_workflow_milvus_config"][
                            "embedder_model_name"
                        ] = args.embedder_model_name
                        current_agent_config["search_workflow_milvus_config"]["embedder_api_key"] = (
                            args.embedder_api_key
                        )
                        current_agent_config["search_workflow_milvus_config"]["embedder_base_url"] = (
                            args.embedder_base_url
                        )
                    current_agent_config = ensure_api_keys_bytearray(current_agent_config)
                asyncio.run(run_jiuwen_workflow(joined_query, current_agent_config, args.report_template))
        elif args.mode in ("template", "all"):
            if not args.file_path:
                parser.print_help()
            elif args.mode == "all" and not args.query:
                parser.print_help()
            else:
                asyncio.run(
                    generate_template_and_run(
                        args.file_path,
                        args.is_template,
                        args.mode,
                        joined_query,
                        current_agent_config,
                    )
                )
    return 0


if __name__ == "__main__":
    main()
