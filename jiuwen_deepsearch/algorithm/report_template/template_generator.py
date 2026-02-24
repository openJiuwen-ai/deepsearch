# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import base64
from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.algorithm.report_template.template_utils import TemplateUtils
from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.config.config import AgentConfig, Config
from jiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context

logger = logging.getLogger(__name__)


class TemplateGenerator:
    BAD_SIGNALS = [
        "Error", "ERROR", "Exception",
        "404 Not Found", "Client error", "Request failed",
    ]
    RETRY_DELAY = 1

    @staticmethod
    async def generate_template(
            file_name: str,
            file_stream: str,
            is_template: bool,
            agent_config: dict,) -> dict:
        """
        Generate a report template from either a sample report or an existing template.
        Args:
            file_name (str): Name of the input file, including its file extension.
            file_stream (str): Base64-encoded string of file content.
            is_template (bool): True if the input file is already a template; False if it is a sample report.
            agent_config (dict): Configuration for the LLM model.
        Returns:
            dict: A result dict containing
            {
                status (str): "success" or "fail".
                template_content (str): The extracted base64-encoded template content.
                error_message (str): The reason for failure, or an empty string if successful.
            }
        """
        try:
            candidate_config = AgentConfig.model_validate(agent_config)
            llm_model_name = candidate_config.llm_config.model_name

            # 注册模型实例
            token = llm_context.set(
                {llm_model_name: create_llm_obj(candidate_config)}
            )

            if is_template:
                suffix = TemplateUtils.valid_template_suffix(file_name)
            else:
                suffix = TemplateUtils.valid_report_suffix(file_name)

            if suffix == ".pdf":
                file_content = TemplateUtils.pdf_base64_to_markdown(file_stream)
            elif suffix == ".docx":
                file_content = TemplateUtils.word_base64_to_markdown(file_stream)
            else:
                file_content = base64.b64decode(file_stream).decode("utf-8")

            processed_output = (
                TemplateUtils.postprocess_structure_keep_content(file_content)
                if is_template
                else await TemplateGenerator._extract_with_llm(file_content, llm_model_name)
            )

            if not is_template:
                if any(sig in processed_output for sig in TemplateGenerator.BAD_SIGNALS):
                    return {
                        "status": "fail",
                        "template_content": "",
                        "error_message": "LLM returned an error instead of template content"
                    }

            encoded_output = base64.b64encode(processed_output.encode("utf-8")).decode("utf-8")
            llm_context.reset(token)
            return {"status": "success", "template_content": encoded_output, "error_message": ""}

        except Exception as e:
            if 'token' in locals():
                llm_context.reset(token)
            if LogManager.is_sensitive():
                logger.error(f"[TemplateGenerator] Generation failed for {file_name}")
                return {"status": "fail", "template_content": "", "error_message": "Template generation failed"}

            logger.error(f"[TemplateGenerator] Generation failed for {file_name}: {e}")
            return {"status": "fail", "template_content": "", "error_message": str(e)}

    @staticmethod
    async def _extract_with_llm(file_content: str, llm_model_name: str) -> str:
        """
        The specific steps of LLM extraction: 
        1. Extract the structure
        2. Extract the semantics
        """
        llm = llm_context.get().get(llm_model_name)
        max_retries = Config().service_config.template_max_generate_retry_num

        processed_structure = await TemplateGenerator._process_step(
            llm=llm,
            prompt_name="template_structure_extract",
            max_retries=max_retries,
            file_content=file_content,
        )
        processed_structure = TemplateUtils.postprocess_structure(processed_structure)

        if not LogManager.is_sensitive():
            logger.info(f"[TemplateGenerator] template structure content: {processed_structure}")

        semantic_output = await TemplateGenerator._process_step(
            llm=llm,
            prompt_name="template_semantic_extract",
            max_retries=max_retries,
            file_content=file_content,
            extra_content=f"Step 1 extracted structure:\n{processed_structure}",
        )
        return semantic_output
    
    @staticmethod
    async def _process_step(
            llm,
            prompt_name: str,
            max_retries: int,
            file_content: str,
            extra_content: str = None,
    ) -> str:
        attempt = 0
        last_exception = None
        processed = ""

        while attempt < max_retries:
            attempt += 1
            logger.info(f"Template extract attempt: {attempt}")

            try:
                messages = apply_system_prompt(prompt_name, context_vars={})
                messages.append({
                    "role": "user",
                    "content": f"Below is the report provided by the user:\n\n{file_content}"
                })

                if extra_content:
                    messages.append({"role": "user", "content": extra_content})

                response = await ainvoke_llm_with_stats(
                    llm, messages, llm_type="basic", agent_name=NodeId.TEMPLATE.value
                )
                processed = response["content"]
                processed = processed.strip() if processed else ""

            except Exception as e:
                last_exception = e
                if LogManager.is_sensitive():
                    logger.warning(
                        f"Template extract Attempt retry {attempt}/{max_retries} failed due to LLM exception"
                    )
                else:
                    logger.warning(
                        f"Template extract Attempt retry {attempt}/{max_retries} failed due to LLM exception: {e}"
                    )
                if attempt < max_retries:
                    continue

            # 内容为空 → retry
            if not processed or not processed.strip():
                last_exception = ValueError("Template extract content is None")
                logger.warning(
                    f"Template extract Attempt {attempt}/{max_retries} returned empty/invalid content, retry ..."
                )
                if attempt < max_retries:
                    continue
                break # 最后一次尝试内容为空，直接退出循环

            if any(sig in processed for sig in TemplateGenerator.BAD_SIGNALS):
                last_exception = ValueError("Template extract returned bad content signal")
                logger.warning(
                    f"Template extract Attempt {attempt}/{max_retries} returned bad content signal, retry ..."
                )
                if attempt < max_retries:
                    continue
                break # 最后一次尝试返回内容包含BAD_SIGNAL，直接退出循环

            return processed

        msg = f"Template extraction failed after retry {max_retries} attempts."
        logger.error(msg)
        raise CustomValueException(StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.code, msg) from last_exception
    