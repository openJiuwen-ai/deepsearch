# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re

from openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research import CitationCheckerResearch
from openjiuwen_deepsearch.common.exception import CustomException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def remove_last_section(markdown_text):
    """
    删除Markdown文本中最后一个标题及其后面的所有内容

    参数:
        markdown_text (str): Markdown格式的文本

    返回:
        str: 处理后的文本
    """
    # 查找所有标题的位置
    headings = []
    for m in re.finditer(r'^#{1,6}\s.+$', markdown_text, re.MULTILINE):
        headings.append((m.start(), m.end()))

    if not headings:
        return markdown_text  # 如果没有标题，返回原文本

    # 获取最后一个标题的位置
    last_heading_end = headings[-1][1]

    # 截取到最后一个标题之前的内容
    return markdown_text[:last_heading_end]


def preprocess_info(report: str, datas: list, language: str) -> dict:
    """
    判断是否要进行溯源校验，封装report和language给后续流程使用

    Args:
        report (str): 待处理的报告文本内容
        datas (list): 引用数据列表，用于检查是否存在引用
        language (str): 报告语言标识，用于确定参考文章标题的语言版本

    Returns:
        dict: 包含是否需要检查标识和响应内容的字典
            - need_check (bool): 是否需要进行溯源校验
            - response_content (dict, optional): 当need_check为True时，包含处理后的报告内容和语言信息
    """
    if not report:
        logger.warning(
            "CitationChecker: empty report, skipped citation check.")
        return dict(need_check=False)

    if len(datas) == 0:
        logger.warning(
            "CitationChecker: empty datas means no inline citation in the text, skipped citation check.")
        return dict(need_check=False)

    report = remove_last_section(report)

    response_content = dict(language=language, article=report)

    return dict(
        need_check=True,
        response_content=response_content
    )


async def postprocess_by_citation_checker(report: dict, datas: list, llm_model: str) -> str:
    """
    用CitationChecker对报告引用后处理，过滤无效、不正确的引用

    Args:
        report (dict): 包含报告内容和语言信息的字典，必须包含'language'字段
        datas (list): 引用数据列表，包含引用的相关信息
        llm_model (str): 使用的语言模型名称，用于初始化ResearchCitationChecker

    Returns:
        - str: 处理成功时返回溯源结果信息,溯源失败raise异常
    """
    logger.info('[CITATION CHECKER] start postprocess')
    if not LogManager.is_sensitive():
        logger.info(
            f"[CITATION CHECKER] original report:\n{json.dumps(report, ensure_ascii=False, indent=4)}")
        logger.info(
            f"[CITATION CHECKER] original datas:\n{json.dumps(datas, ensure_ascii=False, indent=4)}")
    try:
        citation_checker = CitationCheckerResearch(llm_model)
        citation_checker_result_str = await citation_checker.checker(report, datas)
    except CustomException as e:
        logger.error(f'[CITATION CHECKER]: citation checker error: {e}')
        raise e
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f'[CITATION CHECKER]: citation checker error')
        else:
            logger.error(f'[CITATION CHECKER]: citation checker error: {e}')
        raise CustomException(StatusCode.CITATION_CHECKER_POST_PROCESS_ERROR.code,
                              StatusCode.CITATION_CHECKER_POST_PROCESS_ERROR.errmsg.
                              format(e=e)) from e

    if not LogManager.is_sensitive():
        logger.info(
            f"[CITATION CHECKER] citation_checker_result:\n{citation_checker_result_str}")

    logger.info('[CITATION CHECKER] end postprocess')
    return citation_checker_result_str
