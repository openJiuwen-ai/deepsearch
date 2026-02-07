import logging
import argparse
import asyncio
import base64
import os
import uuid
from pathlib import Path

from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.config.method import ExecutionMethod
from jiuwen_deepsearch.framework.jiuwen.agent.agent_factory import AgentFactory
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

LogManager.init(
    log_dir="./logs",
    max_bytes=100 * 1024 * 1024,
    backup_count=20,
    level="DEBUG",
    is_sensitive=False
)
logger = logging.getLogger(__name__)


async def run_jiuwen_workflow(query: str, agent_config: dict, report_template: str):
    '''
    Run the Jiuwen workflow with the given query and agent configuration.

    Args:
        query (str): The input query string.
        agent_config (dict): Configuration for the agent.

    Returns:
        None
    '''
    agent_factory = AgentFactory()
    agent = agent_factory.create_agent(agent_config)
    async for chunk in agent.run(message=query, conversation_id=str(uuid.uuid4()),
                                 report_template=report_template, interrupt_feedback="",
                                 agent_config=agent_config):
        logger.debug("[Stream message from node: %s]", chunk)


def read_file_safely(file_name: str) -> bytes:
    '''
    读取文件，确保文本文件不会出现GBK解码错误。
    - 二进制文件（pdf/docx）rb读取
    - 文本文件（md/txt） utf-8读取
    '''
    if not file_name.lower().endswith('.md'):
        with open(file_name, 'rb') as f:
            return f.read()
    else:
        with open(file_name, 'r', encoding="utf-8") as f:
            return f.read().encode('utf-8')


async def generate_template_and_run(file_name: str, is_template: bool, mode: str, query: str):
    '''
    根据输入文件生成报告模板，并运行Jiuwen工作流。

    Args:
        file_name (str): 样例报告or模板文件路径
        is_template (bool): 是否为模板文件
        mode (str): 模式，支持 "template" 和 "all", template仅生成模板，all先生成模板，再做research
        query (str): 用户输入query

    Returns:
        None
    '''
    agent_config = Config().agent_config.model_dump()
    agent_config['workflow_human_in_the_loop'] = False
    agent_factory = AgentFactory()
    agent = agent_factory.create_agent(agent_config)

    result = await agent.generate_template(
        file_name=file_name,
        file_stream=base64.b64encode(read_file_safely(file_name)).decode('utf-8'),
        is_template=is_template,
        agent_config=agent_config,
    )

    if result.get("status") == "success":
        path = Path(file_name)
        output_name = path.stem + ".md"
        save_path = "./saved_templates/"
        os.makedirs(save_path, exist_ok=True)
        output_path = os.path.join(save_path, output_name)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(base64.b64decode(result["template_content"]).decode('utf-8'))
        logger.info(f"模板已保存至:{output_path}")
    else:
        logger.error("模板生成失败")
        return

    if mode == "all":
        agent_config["llm_config"]["api_key"] = Config().agent_config.llm_config.api_key
        async for chunk in agent.run(
                message=" ".join(query), conversation_id=str(uuid.uuid4()),
                report_template=result["template_content"], interrupt_feedback="",
                agent_config=agent_config):
            logger.debug("[Stream message from node: %s]", chunk)


if __name__ == "__main__":
    '''
    mode:
          - query: 输入query，做research
          - template: 根据输入文件生成报告模板
          - all: 先生成报告模板，再做research
    '''
    parser = argparse.ArgumentParser(description="Run deepsearch workflow")
    parser.add_argument("query", nargs="*", default="AI手机研究报告", help="The query to process")
    parser.add_argument(
        "--mode",
        choices=["query", "template", "all"],
        default="query",
        help="Operation mode: query, template or all"
    )
    parser.add_argument(
        "--search_mode",
        choices=["research"],
        default="research",
        help="must be research"
    )
    parser.add_argument(
        "--execution_method",
        choices=["parallel", "dependency_driving"],
        default="parallel",
        help="exection method of workflow"
    )
    parser.add_argument(
        "--report_template",
        type=str,
        default="",
        help="Base64 encoded report template content for research mode(optional)"
    )
    parser.add_argument(
        "--file_path",
        type=str,
        default="",
        help="样例报告or模板文件路径"
    )
    parser.add_argument(
        "--is_template",
        action="store_true",
        help="Indicates whether the input file is a template"
    )

    args = parser.parse_args()
    if args.mode == "query":
        if not args.query or args.search_mode not in ["research"]:
            parser.print_help()
        else:
            current_agent_config = Config().agent_config.model_dump()
            if args.execution_method.strip() == ExecutionMethod.DEPENDENCY_DRIVING.value:
                current_agent_config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
            else:
                current_agent_config["execution_method"] = ExecutionMethod.PARALLEL.value
            current_agent_config['workflow_human_in_the_loop'] = False
            joined_query = " ".join(args.query)
            asyncio.run(run_jiuwen_workflow(joined_query, current_agent_config, args.report_template))
    elif args.mode in ("template", "all"):
        if not args.file_path:
            parser.print_help()
        if args.mode == "all" and not args.query:
            parser.print_help()
        else:
            asyncio.run(generate_template_and_run(args.file_path, args.is_template, args.mode, args.query))
