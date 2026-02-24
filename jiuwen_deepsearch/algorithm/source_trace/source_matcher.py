# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
from typing import List, Dict, Any, Optional

from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


logger = logging.getLogger(__name__)


async def match_sources(content_recognition_result: List[str],
                        preprocessed_search_record: Dict[str, Any],
                        chunk_size: int,
                        llm_model_name: str) -> Optional[List[Dict[str, Any]]]:
    """匹配内容与搜索记录，对不同类型的搜索记录进行溯源分析。

    Args:
        content_recognition_result (List[str]): 内容识别结果列表，包含需要溯源的句子
        preprocessed_search_record (Dict[str, Any]): 预处理后的搜索记录字典，键为搜索记录类型，值为该类型的搜索记录列表
        chunk_size (int): 分片处理大小，用于处理大量搜索记录时的分块
        llm_model_name (str): LLM模型名称，用于调用大模型进行溯源匹配

    Returns:
        Optional[List[Dict[str, Any]]]: 溯源匹配结果列表，每个结果包含sentence、source和matched_source_indices字段
    """
    if not content_recognition_result:
        return []

    all_trace_results = []

    # 处理所有来源类型
    for source_type, source_list in preprocessed_search_record.items():
        if not isinstance(source_list, list) or not source_list:
            continue
        if not source_type:
            continue

        source_results = await process_source_type(
            source_type, source_list, content_recognition_result, chunk_size, llm_model_name)
        all_trace_results.extend(source_results)

    # 合并溯源结果
    merged_results = merge_trace_results(all_trace_results)

    # 验证和过滤溯源结果
    validated_results = validate_trace_results(
        merged_results, content_recognition_result)
    return validated_results


async def process_source_type(source_type: str, source_list: List[Dict[str, Any]],
                              content_recognition_result: List[str], chunk_size: int,
                              llm_model_name: str) -> List[Dict[str, Any]]:
    """处理单个搜索记录类型的溯源匹配，根据搜索记录列表大小决定是否分片处理。

    Args:
        source_type (str): 搜索记录类型，如搜索记录的分类标识
        source_list (List[Dict[str, Any]]): 该类型的s列表，每个s包含title、url和content等字段
        content_recognition_result (List[str]): 内容识别结果列表
        chunk_size (int): 分片处理大小
        llm_model_name (str): LLM模型名称

    Returns:
        List[Dict[str, Any]]: 该搜索记录类型的溯源结果列表
    """
    if len(source_list) <= chunk_size:
        # 小列表直接处理
        return await process_single_chunk(source_type, source_list, content_recognition_result, llm_model_name)

    # 大列表分片处理
    return await process_chunked_source(source_type, source_list, content_recognition_result,
                                        chunk_size, llm_model_name)


async def process_single_chunk(source_type: str,
                               source_list: List[Dict[str, Any]],
                               content_recognition_result: List[str],
                               llm_model_name: str
                               ) -> List[Dict[str, Any]]:
    """处理单个完整的搜索记录列表，直接调用LLM进行溯源匹配。

    Args:
        source_type (str): 搜索记录类型
        source_list (List[Dict[str, Any]]): 搜索记录列表
        content_recognition_result (List[str]): 内容识别结果列表
        llm_model_name (str): LLM模型名称

    Returns:
        List[Dict[str, Any]]: 溯源结果列表
    """
    search_record = {source_type: source_list}
    return await call_llm_for_trace(source_type, search_record, content_recognition_result, "完整", llm_model_name)


async def process_chunked_source(source_type: str, source_list: List[Dict[str, Any]],
                                 content_recognition_result: List[str], chunk_size: int,
                                 llm_model_name: str) -> List[Dict[str, Any]]:
    """分片处理大的搜索记录列表，将列表分成多个小块分别调用LLM进行溯源匹配。

    Args:
        source_type (str): 搜索记录类型
        source_list (List[Dict[str, Any]]): 搜索记录列表
        content_recognition_result (List[str]): 内容识别结果列表
        chunk_size (int): 分片大小
        llm_model_name (str): LLM模型名称

    Returns:
        List[Dict[str, Any]]: 所有分片的溯源结果合并列表
    """
    all_trace_results = []

    for i in range(0, len(source_list), chunk_size):
        chunk = source_list[i:i + chunk_size]
        search_record = {source_type: chunk}
        chunk_results = await call_llm_for_trace(
            source_type, search_record, content_recognition_result,
            f"分片 {i}-{min(i+chunk_size-1, len(source_list)-1)}", llm_model_name)
        all_trace_results.extend(chunk_results)

    return all_trace_results


async def call_llm_for_trace(source_type: str, search_record: Dict[str, Any],
                       content_recognition_result: List[str], process_type: str,
                       llm_model_name: str) -> List[Dict[str, Any]]:
    """调用LLM进行溯源匹配，构建溯源上下文并处理模型响应。

    Args:
        source_type (str): 搜索记录类型
        search_record (Dict[str, Any]): 搜索记录字典
        content_recognition_result (List[str]): 内容识别结果列表
        process_type (str): 处理类型描述（用于日志）
        llm_model_name (str): LLM模型名称

    Returns:
        List[Dict[str, Any]]: LLM返回的溯源结果列表，异常情况返回空列表
    """
    # 构建溯源专用上下文
    source_tracer_context = dict(
        search_record=json.dumps(search_record, ensure_ascii=False),
        content_recognition_result=content_recognition_result
    )

    try:
        llm = llm_context.get().get(llm_model_name)
        llm_input = apply_system_prompt(
            "source_matching", source_tracer_context)
        llm_output = await ainvoke_llm_with_stats(llm, llm_input,
                                                  agent_name=NodeId.SOURCE_TRACER.value + "_source_matching")
        llm_result = llm_output.get("content", "")

        # 解析并处理结果
        return parse_trace_response(llm_result, source_type)

    except Exception as e:
        # 调用大模型获取引用语句的异常在此处抑制，返回空列表出去，不影响后续流程
        if LogManager.is_sensitive():
            logger.error(
                f"[match_sources] 处理{process_type} {source_type} 时发生错误")
        else:
            logger.error(
                f"[match_sources] 处理{process_type} {source_type} 时发生错误: {e}")
        return []


def parse_trace_response(llm_result: str, source_type: str) -> List[Dict[str, Any]]:
    """解析LLM的溯源响应，提取并处理溯源结果。

    Args:
        llm_result (str): LLM返回的原始结果字符串
        source_type (str): 搜索记录类型，用于设置结果中的source字段

    Returns:
        List[Dict[str, Any]]: 解析后的溯源结果列表，每个结果包含sentence、matched_source_indices和source字段
    """
    # 清理JSON格式
    cleaned_result = normalize_json_output(llm_result)

    # 解析JSON
    trace_results = json.loads(cleaned_result)

    # 提取并设置source字段
    results = []
    if "source_traced_results" in trace_results:
        for result in trace_results["source_traced_results"]:
            result["source"] = source_type
            results.append(result)

    return results


def merge_trace_results(trace_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并相同sentence和source的溯源结果，去重并合并matched_source_indices。

    Args:
        trace_results (List[Dict[str, Any]]): 溯源结果列表

    Returns:
        List[Dict[str, Any]]: 合并后的溯源结果列表，相同句子和来源的结果已合并
    """
    merged_dict = {}

    for result in trace_results:
        sentence = result.get("sentence", "")
        source = result.get("source", "")
        matched_indices = result.get("matched_source_indices", [])
        if not matched_indices:
            continue

        # 创建唯一标识键
        key = f"{sentence}|{source}"

        if key in merged_dict:
            # 如果已存在相同sentence和source的结果，合并matched_source_indices
            existing_indices = merged_dict[key]["matched_source_indices"]
            # 去重并保持顺序
            combined_indices = list(
                set(existing_indices + matched_indices))
            combined_indices.sort()
            merged_dict[key]["matched_source_indices"] = combined_indices
        else:
            # 如果不存在，直接添加
            merged_dict[key] = result.copy()

    # 返回合并后的结果列表
    return list(merged_dict.values())


def validate_trace_results(
    trace_results: List[Dict[str, Any]],
    content_recognition_result: List[str]
) -> List[Dict[str, Any]]:
    """验证和过滤溯源结果，仅保留有效句子的记录。

    Args:
        trace_results (List[Dict[str, Any]]): 待验证的溯源结果列表
        content_recognition_result (List[str]): 内容识别结果列表，包含所有有效句子

    Returns:
        List[Dict[str, Any]]: 验证后的有效溯源结果列表，仅包含存在于content_recognition_result中的句子
    """
    validated_results = []

    for result in trace_results:
        sentence = result.get("sentence", "")
        if sentence and sentence in content_recognition_result:
            validated_results.append(result)
        else:
            if not LogManager.is_sensitive():
                logger.warning(
                    f"[validate_trace_results] 过滤掉无效的关键chunk - 句子不在内容识别结果中: {sentence}")

    logger.info(
        f"[validate_trace_results] 验证后返回 {len(validated_results)} 条有效关键chunk")
    return validated_results
