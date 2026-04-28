# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import json
from typing import List, Dict, NamedTuple, Optional
import base64

from pydantic import BaseModel, Field

from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt, apply_vlm_prompt
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)
MAX_LLM_RETRY_TIMES = 3


def type_check(result, expected_type):
    if not isinstance(result, expected_type):
        error_msg = f"[CHART GENERATION]: 生成结果类型错误, 生成结果类型{type(result)}, 期望类型为{expected_type}"
        raise CustomValueException(StatusCode.CHART_PLACEHOLDER_ERROR.code,
                                    StatusCode.CHART_PLACEHOLDER_ERROR.errmsg.
                                    format(e=error_msg))


def is_equal_length(result, target):
    type_check(result, list)
    if len(result) != target:
        error_msg = f"[CHART GENERATION]: 生成结果数量错误,"
        error_msg += f"生成结果数量{len(result)}, 目标数量{target}"
        raise CustomValueException(StatusCode.CHART_PLACEHOLDER_ERROR.code,
                                    StatusCode.CHART_PLACEHOLDER_ERROR.errmsg.
                                    format(e=error_msg))


class CallModelInput(BaseModel):
    model_name: str = Field(default="", description="模型名称")
    prompt: str = Field(default="", description="prompt文件名")
    user_input: dict = Field(default={}, description="需要处理的输入数据")
    agent_name: str = Field(default=AgentLlmName.VLM_CHART_GENERATOR.value, description="agent名称")


async def call_model(call_model_input: CallModelInput, 
                     detection_func_and_args: dict = None, 
                     use_vlm: bool = False):
    """调用LLM模型处理请求
    调用指定的LLM模型处理用户提示，并返回标准化的JSON格式输出
    Args:
        model_name: llm调用名称
        prompt: prompt文件名
        user_input: 需要处理的输入数据
        detection_func_and_args: 输出检测函数和参数
    Returns:
        str: 标准化的JSON格式输出字符串
    """
    prompt = call_model_input.prompt
    user_input = call_model_input.user_input
    agent_name = call_model_input.agent_name
    model_name = call_model_input.model_name
    
    retries = 0
    while retries < MAX_LLM_RETRY_TIMES:
        try:
            if use_vlm:
                user_prompt = apply_vlm_prompt(prompt, user_input, [user_input.get("chart_base64", "")])
            else:
                user_prompt = apply_system_prompt(prompt, user_input)
            llm = llm_context.get().get(model_name)
            response = await ainvoke_llm_with_stats(llm, user_prompt, agent_name=agent_name)
            content = response.get("content", "")
            
            if not detection_func_and_args or detection_func_and_args.get("option", "") != "skip normalize":
                content = normalize_json_output(content)
                content = json.loads(content.replace("```json", "").replace("```", ""))
            if detection_func_and_args and detection_func_and_args.get("detection_func", None):
                # 需要对输出进行检验
                detection_func = detection_func_and_args.get("detection_func")
                params = detection_func_and_args.get("args", {})
                detection_func(content, params)
            return content
        except CustomValueException as e:
            retries += 1
            logger.warning(f'[CHART GENERATION] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'call_model error {e}')
        except Exception as e:
            retries += 1
            if LogManager.is_sensitive():
                logger.warning(f'[CHART GENERATION] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'call_model error')
            else:
                logger.warning(f'[CHART GENERATION] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'call_model error {e}')
    
    logger.error(f'[CHART GENERATION] retry {MAX_LLM_RETRY_TIMES} times, call_model error')
    return []


def get_chart_base64(chart_path: str) -> Optional[str]:
    """
    获取图表的base64编码

    Args:
        chart_path: 图表路径

    Returns:
        Optional[str]: base64编码
    """
    try:
        with open(chart_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Error reading chart: {e}")
        return None


def save_chart_file(chart_base64: str, file_name: str, file_path: str) -> Optional[str]:
    """
    将base64编码的图表数据解码并保存为PNG图像文件

    Args:
        chart_base64: 图表的base64编码字符串
        file_name: 保存的文件名（不含扩展名）
        file_path: 文件保存的目录路径

    Returns:
        Optional[str]: 保存成功时返回完整文件路径，失败时返回None
    """
    import os

    try:
        # 解码base64字符串
        image_data = base64.b64decode(chart_base64)

        # 确保目录存在
        os.makedirs(file_path, exist_ok=True)

        # 构建完整文件路径
        full_path = os.path.join(file_path, f"{file_name}.png")

        # 写入文件
        with open(full_path, "wb") as f:
            f.write(image_data)

        logger.info(f"[CHART GENERATION] Chart saved successfully: {full_path}")
        return full_path
    except Exception as e:
        logger.error(f"[CHART GENERATION] Error saving chart: {e}")
        return None
