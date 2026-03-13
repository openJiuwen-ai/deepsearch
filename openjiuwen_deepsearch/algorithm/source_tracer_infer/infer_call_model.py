# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import json
from dataclasses import dataclass, field
from typing import List, Dict, NamedTuple, Set

from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)
MAX_LLM_RETRY_TIMES = 3


class GraphInfo(NamedTuple):
    structured_inference: List[List]
    node_map: Dict
    citation_ids: List[int]
    conclusion_ids: List[int]


@dataclass
class NumberNodeParam:
    """
    用于在编号过程中在多个函数之间传递的中间状态。
    使用 dataclass + default_factory，避免可变默认参数在不同实例之间共享。
    """
    node_set: Set = field(default_factory=set)
    node_map: Dict = field(default_factory=dict)
    node_index: int = 0
    citation_ids: Set[int] = field(default_factory=set)
    conclusion_ids: Set[int] = field(default_factory=set)



def type_check(result, expected_type):
    if not isinstance(result, expected_type):
        error_msg = f"[SOURCE TRACER INFER]: 生成结果类型错误, 生成结果类型{type(result)}, 期望类型为{expected_type}"
        raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                    StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                    format(e=error_msg))


def is_equal_length(result, target):
    type_check(result, list)
    for r in result:
        type_check(r, list)
        if len(r) != target:
            error_msg = f"[SOURCE TRACER INFER]: 生成结果数量错误,"
            error_msg += f"生成结果数量{len(result)}, 目标数量{target}"
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.errmsg.
                                        format(e=error_msg))


async def call_model(model_name: str, prompt: str, user_input: dict, 
                     detection_func_and_args: dict = None, 
                     agent_name: str = NodeId.SOURCE_TRACER_INFER.value):
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
    retries = 0
    while retries < MAX_LLM_RETRY_TIMES:
        try:
            user_prompt = apply_system_prompt(prompt, user_input)
            llm = llm_context.get().get(model_name)
            response = await ainvoke_llm_with_stats(llm, user_prompt, agent_name=agent_name)
            content = response.get("content", "")
            content = normalize_json_output(content)
            llm_result = json.loads(content.replace("```json", "").replace("```", ""))
            if detection_func_and_args:
                # 需要对输出进行检验
                detection_func = detection_func_and_args.get("detection_func")
                params = detection_func_and_args.get("args")
                detection_func(llm_result, params)
            return llm_result
        except CustomValueException as e:
            retries += 1
            logger.warning(f'[SOURCE TRACER INFER] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'call_model error {e}')
        except Exception as e:
            retries += 1
            if LogManager.is_sensitive():
                logger.warning(f'[SOURCE TRACER INFER] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'call_model error')
            else:
                logger.warning(f'[SOURCE TRACER INFER] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'call_model error {e}')
    
    logger.error(f'[SOURCE TRACER INFER] retry {MAX_LLM_RETRY_TIMES} times, call_model error')
    return []