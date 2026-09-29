# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import json
from dataclasses import dataclass, field
from typing import Any, List, Dict, NamedTuple, Set
import copy

from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

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
    
    tail_id: int = -1
    head_id_list: List = field(default_factory=list)
    structured_inference: List = field(default_factory=list)

    def update_structured_inference(self, relation: str):
        self.structured_inference.append([copy.deepcopy(self.head_id_list), relation, self.tail_id])


def type_check(result, expected_type):
    """校验结果类型是否符合预期类型。"""
    if not isinstance(result, expected_type):
        error_msg = f"[SOURCE TRACER INFER]: 生成结果类型错误, 生成结果类型{type(result)}, 期望类型为{expected_type}"
        raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                    StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                    format(e=error_msg))


def is_list_of(result, expected_type=int):
    """校验结果为 list 且每个元素为指定非 bool 类型，避免嵌套 list / dict / str 通过顶层 type_check。

    - 针对 int: expected_type=int，同时排除 bool（Python 中 bool 是 int 子类）。
    - 针对 str: expected_type=str。
    """
    if not isinstance(result, list):
        error_msg = (
            f"[SOURCE TRACER INFER]: 生成结果类型错误, "
            f"生成结果类型{type(result)}, 期望类型为 list[{expected_type.__name__}]"
        )
        raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                    StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                    format(e=error_msg))
    for i, item in enumerate(result):
        if not isinstance(item, expected_type) or isinstance(item, bool):
            error_msg = (
                f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                f"索引 {i}: 元素类型{type(item)}, 期望类型为 {expected_type.__name__} (非 bool)"
            )
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                        format(e=error_msg))


def is_equal_length(result, target):
    """校验结果是否为固定长度的结构。"""
    type_check(result, list)
    for r in result:
        type_check(r, list)
        if len(r) != target:
            error_msg = f"[SOURCE TRACER INFER]: 生成结果数量错误,"
            error_msg += f"生成结果数量{len(result)}, 目标数量{target}"
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.errmsg.
                                        format(e=error_msg))


def _normalize_decimal_node_id(value: Any) -> Any:
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return value


def normalize_structured_triple_reference_ids(result: Any) -> Any:
    """Convert quoted reference IDs in structured-triple heads to integers.

    Only head entries are normalized because structured-triple tails are
    conclusion text by contract and must remain strings. Mutates and returns
    ``result`` when it is a list.
    """
    if not isinstance(result, list):
        return result

    for triple in result:
        if not isinstance(triple, list) or len(triple) != 3:
            continue
        if isinstance(triple[0], list):
            triple[0] = [_normalize_decimal_node_id(head) for head in triple[0]]
    return result


def normalize_supplement_triple_node_ids(result: Any) -> Any:
    """Convert decimal string node IDs in supplement triples to integers.

    Only the head/tail node-ID positions are normalized. Other values are left
    untouched so the strict validator can still reject malformed model output.
    Mutates and returns ``result`` when it is a list.
    """
    if not isinstance(result, list):
        return result

    for triple in result:
        if not isinstance(triple, list) or len(triple) != 3:
            continue
        if isinstance(triple[0], list):
            triple[0] = [_normalize_decimal_node_id(node_id) for node_id in triple[0]]
        triple[2] = _normalize_decimal_node_id(triple[2])
    return result


def is_valid_structured_triples(result: Any, target: int = 3) -> None:
    """Validate reasoning triples with textual conclusions."""
    type_check(result, list)
    for i, triple in enumerate(result):
        type_check(triple, list)
        if len(triple) != target:
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果数量错误,"
                         f"索引 {i}: 三元组数量{len(triple)}, 目标数量{target}")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.code,
                                       StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.errmsg.format(e=error_msg))
        heads, relation, tail = triple
        if not isinstance(heads, list) or not heads:
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                         f"索引 {i}: 头实体类型{type(heads)}, 期望类型为非空 list[int | str]")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                       StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.format(e=error_msg))
        for head in heads:
            if isinstance(head, bool) or not isinstance(head, (int, str)):
                error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                             f"索引 {i}: 头实体类型{type(head)}, 期望类型为 int | str (非 bool)")
                raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                           StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.format(e=error_msg))
        if isinstance(relation, bool) or not isinstance(relation, str):
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                         f"索引 {i}: 关系类型{type(relation)}, 期望类型为 str")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                       StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.format(e=error_msg))
        if isinstance(tail, bool) or not isinstance(tail, str):
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                         f"索引 {i}: 尾实体类型{type(tail)}, 期望类型为 str")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                       StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.format(e=error_msg))


def is_valid_supplement_triples(result, target=3):
    """校验补边三元组列表：result 为 list[[int, ...], str, int]，每个三元组长度为 target。

    消费端假设（supplement_graph）：
    - new_tuple[0] 必须可下标且非空（取 new_tuple[0][0] 与连通分量比对）；
    - new_tuple[0] 元素与 new_tuple[2] 必须是可哈希的非 bool int（用于 set 成员判断）；
    - new_tuple[1] 必须是 str（作为图的边 label）。
    """
    type_check(result, list)
    for i, triple in enumerate(result):
        type_check(triple, list)
        if len(triple) != target:
            error_msg = f"[SOURCE TRACER INFER]: 生成结果数量错误,"
            error_msg += f"索引 {i}: 三元组数量{len(triple)}, 目标数量{target}"
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_LEN_ERROR.errmsg.
                                        format(e=error_msg))
        head_ids, relation, tail_id = triple
        if not isinstance(head_ids, list) or not head_ids:
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                         f"索引 {i}: 头实体类型{type(head_ids)}, 期望类型为非空 list[int]")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                        format(e=error_msg))
        for head_id in head_ids:
            if not isinstance(head_id, int) or isinstance(head_id, bool):
                error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                             f"索引 {i}: 头实体节点类型{type(head_id)}, 期望类型为 int (非 bool)")
                raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                            StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                            format(e=error_msg))
        if not isinstance(relation, str) or isinstance(relation, bool):
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                         f"索引 {i}: 关系类型{type(relation)}, 期望类型为 str")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                        format(e=error_msg))
        if not isinstance(tail_id, int) or isinstance(tail_id, bool):
            error_msg = (f"[SOURCE TRACER INFER]: 生成结果元素类型错误, "
                         f"索引 {i}: 尾实体类型{type(tail_id)}, 期望类型为 int (非 bool)")
            raise CustomValueException(StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.code,
                                        StatusCode.SOURCE_TRACER_INFER_DATA_TYPE_ERROR.errmsg.
                                        format(e=error_msg))


async def call_model(model_name: str, prompt: str, user_input: dict, 
                     detection_func_and_args: dict = None, 
                     agent_name: str = AgentLlmName.SOURCE_TRACER_INFER.value):
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
            user_prompt = build_prompt_messages(prompt, user_input)
            llm = llm_context.get().get(model_name)
            response = await ainvoke_llm_with_stats(llm, user_prompt, agent_name=agent_name)
            content = response.get("content", "")
            content = normalize_json_output(content)
            llm_result = json.loads(content.replace("```json", "").replace("```", ""))
            if detection_func_and_args:
                normalizer = detection_func_and_args.get("normalizer")
                if normalizer is not None:
                    llm_result = normalizer(llm_result)
                # 需要对输出进行检验
                detection_func = detection_func_and_args.get("detection_func")
                params = detection_func_and_args.get("args")
                if params is not None:
                    detection_func(llm_result, params)
                else:
                    detection_func(llm_result)
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
