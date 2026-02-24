# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
from typing import Any, Dict

from openjiuwen_deepsearch.algorithm.source_trace.add_source import (add_source_references,
                                                                     generate_source_datas,
                                                                     merge_source_datas)
from openjiuwen_deepsearch.algorithm.source_trace.content_analyzer import recognize_content_to_cite
from openjiuwen_deepsearch.algorithm.source_trace.source_matcher import match_sources
from openjiuwen_deepsearch.algorithm.source_trace.source_tracer_preprocessors import (generate_origin_report_data,
                                                                                      preprocess_report,
                                                                                      preprocess_search_record)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


class SourceTracer:
    """文档溯源处理器，用于为文档中的句子添加来源引用信息。"""

    def __init__(self, algorithm_inputs: dict):
        """初始化SourceTracer实例，设置溯源所需的参数和数据源。

        Args:
            algorithm_inputs (dict): 进行溯源必要的输入数据字典，包含以下键：
                - report (str): 需要进行溯源的报告文本
                - classified_content (list): 用于溯源生成，子报告生成过程中使用的top-K文章信息
                - llm_model_name (str): 使用的LLM模型名称
        """
        self._similarity_threshold = 0.9
        self._search_record_max_content_len = 3000
        self._chunk_size = 40
        self._report = algorithm_inputs.get("report", "")
        self._classified_content = algorithm_inputs.get("classified_content", [])
        self._search_record = self.transform_search_record(self._classified_content)
        self._llm_model_name = algorithm_inputs.get("llm_model_name", "")
        self._trace_source_datas = []

    @staticmethod
    def transform_search_record(classified_content: list) -> dict:
        """将子报告生成过程中使用的top-K文章信息转换为溯源模块使用的搜索记录格式。

        Args:
            classified_content (list): 子报告生成过程中使用的子报告生成过程中使用的top-K文章信息，每个元素为包含url、title和original_content的字典

        Returns:
            dict: 溯源模块使用的搜索记录，格式为{'search_record': [{'url': str, 'title': str, 'content': str}, ...]}
        """
        if not classified_content:
            return {}
        filtered_content = []
        for item in classified_content:
            if isinstance(item, dict):
                # 检查是否同时拥有url、title、original_content三个字段
                if "url" in item and "title" in item and "original_content" in item:
                    filtered_item = {
                        "url": item["url"],
                        "title": item["title"],
                        # 将original_content改为content，方便后续识别使用
                        "content": item["original_content"]
                    }
                    filtered_content.append(filtered_item)

        search_record = dict(search_record=filtered_content)
        return search_record

    async def research_trace_source(self) -> None:
        """在research模式下对报告进行溯源，生成引用信息data列表。

        Returns:
            None
        """
        try:
            # 如果原report为空，直接返回
            if not self._report:
                logger.warning("[research_trace_source] report为空,不做溯源")
                return

            # 预处理report, 针对research模式，需要删除最后的参考文献章节，这里提取出的report仅用于引用识别
            _, preprocessed_report = preprocess_report(self._report)

            # 预处理搜索记录
            preprocessed_search_record = preprocess_search_record(self._search_record,
                                                                  self._search_record_max_content_len)
            if not preprocessed_search_record:
                logger.warning("[research_trace_source] 预处理搜索记录失败，退出溯源")
                return
            if not LogManager.is_sensitive():
                logger.debug(
                    f"[research_trace_source] 预处理后的搜索记录: %s",
                    json.dumps(preprocessed_search_record, ensure_ascii=False, indent=2))

            # 识别需要引用的内容
            content_recognition_result = await recognize_content_to_cite(
                preprocessed_report, self._similarity_threshold, self._llm_model_name)
            if not content_recognition_result:
                logger.warning("[research_trace_source] 未识别到需要增加引用的内容")
                return

            # 获取溯源匹配结果
            trace_results = await match_sources(
                content_recognition_result,
                preprocessed_search_record,
                self._chunk_size,
                self._llm_model_name
            )
            if not trace_results:
                logger.warning("[research_trace_source] 未获取到有效溯源结果")
                return

            # 生成溯源引用信息datas
            datas = generate_source_datas(preprocessed_report, preprocessed_search_record, trace_results)
            self._trace_source_datas = datas

        except Exception as e:
            raise CustomValueException(StatusCode.SOURCE_TRACER_TRACE_SOURCE_ERROR.code,
                                       StatusCode.SOURCE_TRACER_TRACE_SOURCE_ERROR.errmsg.format(e=str(e))) from e

    def add_source_to_report(self) -> Dict[str, Any]:
        """将溯源引用信息添加到报告文本中，生成带有引用标记的报告。

        Returns:
            Dict[str, Any]: 处理结果字典，包含以下键：
                - modified_report (str): 增加引用标记后的报告文本
                - datas (list): 合并后的引用信息列表，包含所有溯源数据源
        """
        try:
            datas = self._trace_source_datas
            # 针对完整report，先进行预处理，删除文末参考文献章节
            removed_section, preprocessed_report = preprocess_report(self._report)

            # 提取原始报告中已经存在的引用内容
            origin_report_dict = generate_origin_report_data(
                preprocessed_report, self._classified_content)
            origin_report_datas = origin_report_dict.get("origin_report_data", [])
            need_add_source_report = origin_report_dict.get("modified_report", "")

            # 对文中自带的引用内容和传入的引用信息列表做合并排序
            all_datas = merge_source_datas(
                need_add_source_report, datas, origin_report_datas)

            # 使用datas列表，对原始报告文本进行来源引用添加
            added_source_report, all_datas = add_source_references(need_add_source_report, all_datas)
            added_source_report = added_source_report + removed_section

            if not LogManager.is_sensitive():
                logger.info(f'[add_source_to_report] 添加来源引用后的报告 {added_source_report}')
                logger.debug(f'[add_source_to_report] 合并后的引用信息: %s',
                             json.dumps(all_datas, ensure_ascii=False, indent=2))
            return dict(modified_report=added_source_report, datas=all_datas)
        except Exception as e:
            raise CustomValueException(StatusCode.SOURCE_TRACER_ADD_SOURCE_ERROR.code,
                                       StatusCode.SOURCE_TRACER_ADD_SOURCE_ERROR.errmsg.format(e=str(e))) from e
