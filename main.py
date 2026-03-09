import argparse
import asyncio
import base64
import copy
import json
import logging
import os
import uuid
from pathlib import Path
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.utils.debug_utils.result_exporter import ResultExporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

LogManager.init(
    log_dir="./output/logs",
    max_bytes=100 * 1024 * 1024,
    backup_count=20,
    level="DEBUG",
    is_sensitive=False
)

ResultExporter.init(
    results_dir="./output/results"
)

logger = logging.getLogger(__name__)


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
    agent_factory = AgentFactory()
    agent = agent_factory.create_agent(agent_config)
    async for chunk in agent.run(message=query, conversation_id=str(uuid.uuid4()),
                                 report_template=report_template, interrupt_feedback="",
                                 agent_config=agent_config):
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


if __name__ == "__main__":
    """
    mode:
          - query: 输入query，做research
          - template: 根据输入文件生成报告模板
          - all: 先生成报告模板，再做research
          
    启动示例（根据用户查询生成研究报告）:
    uv run main.py \
        --mode query \
        --query "Your research question here" \
        --llm_model_name your_llm_model_name \
        --llm_model_type your_llm_model_type \
        --llm_base_url your_llm_api_base_url \
        --llm_api_key your_llm_api_key \
        --web_search_engine_name your_web_search_engine_name \
        --web_search_api_key your_web_search_api_key \
        --web_search_url your_web_search_api_url
        
    启动示例（根据用户查询和用户已有模板生成研究报告）:
    uv run main.py \
        --mode all \
        --query "Your research question here" \
        --llm_model_name your_llm_model_name \
        --llm_model_type your_llm_model_type \
        --llm_base_url your_llm_api_base_url \
        --llm_api_key your_llm_api_key \
        --web_search_engine_name your_web_search_engine_name \
        --web_search_api_key your_web_search_api_key \
        --web_search_url your_web_search_api_url \
        --file_path path_to_your_sample_report_or_template_file \
        --is_template (include this flag if the input file is already a template)
    """
    parser = argparse.ArgumentParser(description="Run deepsearch workflow")
    parser.add_argument("--query", nargs="*", default="AI手机研究报告", help="The query to process")
    parser.add_argument("--mode", choices=["query", "template", "all"], default="query",
                        help="Operation mode: query, template or all")
    parser.add_argument("--search_mode", choices=["research"], default="research", help="must be research")
    parser.add_argument("--execution_method", choices=["parallel", "dependency_driving"], default="parallel",
                        help="execution method of workflow")
    parser.add_argument("--report_template", type=str, default="",
                        help="Base64 encoded report template content for research mode(optional)")
    parser.add_argument("--file_path", type=str, default="", help="样例报告or模板文件路径")
    parser.add_argument("--is_template", action="store_true", help="Indicates whether the input file is a template")

    # 添加llm配置参数
    parser.add_argument("--llm_model_name", type=str, required=True, help="llm 模型名称")
    parser.add_argument("--llm_model_type", type=str, required=True, help="llm 模型类型，openai or siliconflow")
    parser.add_argument("--llm_base_url", type=str, required=True, help="llm 模型服务地址")
    parser.add_argument("--llm_api_key", type=str, required=True, help="llm 模型密钥")

    # 添加搜索引擎配置参数
    parser.add_argument("--web_search_engine_name", type=str, required=True, help="web 搜索引擎名称, tavily or google")
    parser.add_argument("--web_search_api_key", type=str, required=True, help="web 搜索引擎密钥")
    parser.add_argument("--web_search_url", type=str, required=True, help="web 搜索引擎服务地址")
    parser.add_argument("--max_web_search_results", type=int, default=5, help="web 搜索单次请求返回结果数量")

    # 添加SSL校验和证书参数
    parser.add_argument("--llm_ssl_verify", action="store_true", help="开启 LLM SSL 校验")
    parser.add_argument("--llm_ssl_cert", type=str, default="", help="LLM SSL 证书")
    parser.add_argument("--tool_ssl_verify", action="store_true", help="开启 Tool SSL 校验")
    parser.add_argument("--tool_ssl_cert", type=str, default="", help="Tool SSL 证书")

    args = parser.parse_args()
    if args.llm_ssl_verify and not args.llm_ssl_cert:
        parser.error("开启 --llm_ssl_verify 时必须提供 --llm_ssl_cert")

    if args.tool_ssl_verify and not args.tool_ssl_cert:
        parser.error("开启 --tool_ssl_verify 时必须提供 --tool_ssl_cert")
    joined_query = " ".join(args.query)

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

    # 解析搜索引擎配置
    current_agent_config["web_search_engine_config"]["search_engine_name"] = args.web_search_engine_name
    current_agent_config["web_search_engine_config"]["search_api_key"] = bytearray(args.web_search_api_key,
                                                                                   encoding="utf-8")
    current_agent_config["web_search_engine_config"]["search_url"] = args.web_search_url
    current_agent_config["web_search_engine_config"]["max_web_search_results"] = args.max_web_search_results

    current_agent_config["workflow_human_in_the_loop"] = False
    current_agent_config["search_mode"] = args.search_mode
    if args.execution_method.strip() == ExecutionMethod.DEPENDENCY_DRIVING.value:
        current_agent_config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
    else:
        current_agent_config["execution_method"] = ExecutionMethod.PARALLEL.value

    if args.mode == "query":
        if not args.query or args.search_mode not in ["research", "search"]:
            parser.print_help()
        else:
            asyncio.run(run_jiuwen_workflow(joined_query, current_agent_config, args.report_template))
    elif args.mode in ("template", "all"):
        if not args.file_path:
            parser.print_help()
        if args.mode == "all" and not args.query:
            parser.print_help()
        else:
            asyncio.run(generate_template_and_run(
                args.file_path, args.is_template, args.mode, joined_query, current_agent_config))
