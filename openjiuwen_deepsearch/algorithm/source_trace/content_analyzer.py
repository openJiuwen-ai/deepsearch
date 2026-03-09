# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import difflib
from typing import List

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences

logger = logging.getLogger(__name__)


async def recognize_content_to_cite(modified_report: str, similarity_threshold: float,
                                    llm_model_name: str) -> List[str]:
    """识别报告中需要引用的内容
    调用LLM模型从修改后的报告中提取需要添加引用的关键内容片段，
    并通过相似度验证确保提取的内容与报告原文一致或高度相似。

    Args:
        modified_report (str): 修改后的报告文本，需要从中识别需要引用的内容
        similarity_threshold (float): 相似度阈值，用于判断提取的内容与报告原文的相似程度
        llm_model_name (str): LLM模型名称，用于调用大模型进行内容识别

    Returns:
        List[str]: 需要引用的句子列表，每个元素都是报告中需要添加引用的句子
    """

    # 构建溯源专用上下文
    source_tracer_context = dict(report=modified_report)

    try:
        llm = llm_context.get().get(llm_model_name)
        llm_input = apply_system_prompt(
            "content_recognition", source_tracer_context)
        llm_output = await ainvoke_llm_with_stats(llm, llm_input,
                                                  agent_name=NodeId.SOURCE_TRACER.value + "_content_recognition")
        llm_result = llm_output.get("content", "")
        llm_result = normalize_json_output(llm_result)

        # 处理LLM返回结果，确保每个句子都在报告中存在或有高度相似的句子
        result_sentences_list = validate_and_enhance_sentences(
            llm_result, modified_report, similarity_threshold)

    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f"[recognize_content_to_cite] LLM调用过程中发生错误")
        else:
            logger.error(f"[recognize_content_to_cite] LLM调用过程中发生错误: {e}")
        return []

    logger.info(
        f"[recognize_content_to_cite] 提取结果：提取到关键原文片段chunk数量{len(result_sentences_list)}")
    if not LogManager.is_sensitive():
        logger.info(
            f"[recognize_content_to_cite] 识别到需要添加引用的内容: {result_sentences_list}")

    return result_sentences_list


def validate_and_enhance_sentences(llm_result: str, report: str, similarity_threshold: float) -> List[str]:
    """验证并增强LLM识别到的句子列表
    解析LLM返回的JSON结果，验证每个句子是否在报告中存在或有高度相似的句子，
    并对结果进行去重处理，确保最终返回的句子列表准确可靠。

    Args:
        llm_result (str): content_recognition返回的JSON字符串，包含识别到的需要引用的句子列表
        report (str): 原始报告文本，用于验证识别到的句子
        similarity_threshold (float): 相似度阈值，用于判断两个句子的相似程度

    Returns:
        List[str]: 验证并增强后的句子列表，确保每个句子都在报告中存在或有高度相似的句子
    """
    # 解析JSON结果
    result_data = json.loads(llm_result)
    sentences = result_data.get("sentences", [])

    # 处理每个句子
    processed_sentences = []
    seen_sentences = set()  # 用于去重

    for sentence in sentences:
        # 1. 尝试在报告中找到完全匹配的句子
        if sentence in report:
            if sentence not in seen_sentences:
                processed_sentences.append(sentence)
                seen_sentences.add(sentence)
        else:
            # 2. 尝试找到相似度极高的句子
            similar_sentence = find_similar_sentence(
                sentence, report, similarity_threshold)
            if similar_sentence and similar_sentence not in seen_sentences:
                processed_sentences.append(similar_sentence)
                seen_sentences.add(similar_sentence)

    return processed_sentences


def find_similar_sentence(sentence: str, report: str, similarity_threshold: float) -> str:
    """在报告中查找与输入句子相似度最高且超过阈值的句子
    将报告分割成句子，使用difflib.SequenceMatcher计算每个句子与输入句子的相似度，
    返回相似度最高且超过指定阈值的句子，如果没有找到则返回空字符串。

    Args:
        sentence (str): 要查找相似的句子
        report (str): 要在其中查找的报告文本
        similarity_threshold (float): 相似度阈值，只有相似度大于等于该值的句子才会被返回

    Returns:
        str: 找到的相似度最高且超过阈值的句子，如果没有找到则返回空字符串
    """
    # 将报告分割成句子
    report_sentences = split_into_sentences(report)

    # 初始化最大相似度和对应的句子
    max_similarity = 0
    similar_sentence = ""

    # 对每个报告中的句子计算相似度
    for report_sent in report_sentences:
        # 使用difflib计算相似度
        similarity = difflib.SequenceMatcher(
            None, sentence, report_sent).ratio()

        # 如果相似度高于当前最大值且达到阈值，则更新最大相似度和对应的句子
        if similarity > max_similarity and similarity >= similarity_threshold:
            max_similarity = similarity
            similar_sentence = report_sent

    # 如果找到了相似度足够高的句子，则返回
    if similar_sentence:
        return similar_sentence

    # 如果没有找到相似度足够高的句子，则返回空字符串
    return ""
