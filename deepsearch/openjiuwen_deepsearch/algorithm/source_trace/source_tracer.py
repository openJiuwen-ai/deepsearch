# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
from typing import Any, Dict, Tuple

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
from openjiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences
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
        self._min_origin_coverage_count: int = 10
        self._min_origin_coverage_ratio: float = 0.3
        self._report = algorithm_inputs.get("report", "")
        self._classified_content = algorithm_inputs.get("classified_content", [])
        self._search_record = self.transform_search_record(self._classified_content)
        self._llm_model_name = algorithm_inputs.get("llm_model_name", "")
        self._trace_source_datas = []

    @staticmethod
    def transform_search_record(classified_content: list) -> dict:
        """将子报告生成过程中使用的top-K文章信息转换为溯源模块使用的搜索记录格式。

        Args:
            classified_content (list): 子报告生成过程中使用的top-K文章信息，
                每个元素为包含doc_url/doc_title/passage_text或url/title/original_content的字典

        Returns:
            dict: 溯源模块使用的搜索记录，格式为{'search_record': [{'url': str, 'title': str, 'content': str}, ...]}
        """
        if not classified_content:
            logger.warning("[TRACE_DIAG] transform_search_record: classified_content 为空, search_record 将为空")
            return {}
        filtered_content = []
        skipped_non_dict = 0
        skipped_missing_field = 0
        sample_keys = None
        # Note: do NOT deduplicate by URL here. In passage-level mode the same
        # URL legitimately carries multiple distinct passage_text entries, and
        # dropping them here starves match_sources of supporting evidence and
        # reduces generated references. Exact duplicates (same url+title+content)
        # are handled later by is_duplicate_record in preprocess_search_record.
        for item in classified_content:
            if not isinstance(item, dict):
                skipped_non_dict += 1
                continue
            if sample_keys is None:
                sample_keys = sorted(list(item.keys()))
            url = item.get("url") or item.get("doc_url") or ""
            title = item.get("title") or item.get("doc_title") or ""
            content = item.get("original_content") or item.get("passage_text") or ""
            if url and title and content:
                filtered_item = {
                    "url": url,
                    "title": title,
                    "content": content,
                }
                filtered_content.append(filtered_item)
            else:
                skipped_missing_field += 1

        logger.info(
            "[TRACE_DIAG] transform_search_record: input=%d, output=%d, "
            "skipped_non_dict=%d, skipped_missing_field=%d, sample_keys=%s",
            len(classified_content), len(filtered_content), skipped_non_dict, skipped_missing_field, sample_keys,
        )
        search_record = dict(search_record=filtered_content)
        return search_record

    @staticmethod
    def _filter_meaningful_sentences(sentences: list) -> list:
        """过滤无意义的句子，保留实际内容用于覆盖率计算。

        过滤规则：
        - 空字符串或仅含空白字符的行
        - Markdown标题行（以#开头）
        - Markdown表格分隔行（仅含|、-、:、空格）
        - Markdown表格数据行（以|开头）
        - 代码块标记行（以```开头）

        Args:
            sentences (list): split_into_sentences 返回的原始句子列表

        Returns:
            list: 仅包含有意义内容句子的列表
        """
        meaningful = []
        for s in sentences:
            stripped = s.strip()
            if not stripped:
                continue
            # Markdown标题行
            if stripped.startswith('#'):
                continue
            # 代码块标记行
            if stripped.startswith('```'):
                continue
            # Markdown表格分隔行（仅含 |、-、:、空格）
            if re.match(r'^[\s|:-]+$', stripped):
                continue
            meaningful.append(s)
        return meaningful

    @staticmethod
    def _calculate_coverage(report: str) -> Tuple[float, int]:
        """通过检测引用标记计算报告文本的覆盖率及被覆盖句子数。

        使用 [citation:X] 标记检测代替文本子串匹配，避免误报和漏报：
        - 零误报：只有真正带引用标记的句子才算覆盖
        - 零漏报：每个带引用标记的句子都会被检测到

        Args:
            report (str): 预处理后的报告文本（含 [citation:X] 标记）

        Returns:
            Tuple[float, int]: (覆盖率, 被覆盖句子数)，total_sentences为0时返回(0.0, 0)
        """
        sentences = split_into_sentences(report)
        meaningful_sentences = SourceTracer._filter_meaningful_sentences(sentences)
        total_sentences = len(meaningful_sentences)
        if total_sentences == 0:
            return 0.0, 0

        # 检测句子中的引用标记 [citation:X]，精确判断覆盖
        citation_pattern = re.compile(r'\[\s*citation:\s*\d+\s*\]')
        covered_count = sum(
            1 for sentence in meaningful_sentences
            if citation_pattern.search(sentence)
        )

        return covered_count / total_sentences, covered_count

    def pre_check_origin_coverage(self) -> dict:
        """前置检查报告被原始引用的覆盖情况，判断是否需要执行溯源。

        Returns:
            dict: 包含5个字段的检查结果：
                - need_generate (bool): True=需要执行research_trace_source, False=可跳过
                - origin_count (int): 被引用覆盖的唯一句子数
                - total_sentences (int): 报告中有意义的句子总数
                - coverage (float): 覆盖率
                - reason (str): 跳过或执行的原因说明
        """
        # 前置判断：空报告 → 不需要生成
        if not self._report:
            return {
                "need_generate": False,
                "origin_count": 0,
                "total_sentences": 0,
                "coverage": 0.0,
                "reason": "empty report"
            }

        # 前置判断：无 classified_content → 无法生成新引用，跳过溯源
        if not self._classified_content:
            return {
                "need_generate": False,
                "origin_count": 0,
                "total_sentences": 0,
                "coverage": 0.0,
                "reason": "no classified content"
            }

        # 预处理报告
        _, preprocessed_report = preprocess_report(self._report)

        # 通过引用标记检测计算覆盖率（无需 generate_origin_report_data）
        coverage, covered_count = self._calculate_coverage(preprocessed_report)

        # 被覆盖的唯一句子数
        origin_count = covered_count

        # 计算有意义句子总数
        sentences = split_into_sentences(preprocessed_report)
        meaningful_sentences = self._filter_meaningful_sentences(sentences)
        total_sentences = len(meaningful_sentences)

        # 判断逻辑
        if origin_count >= self._min_origin_coverage_count and coverage >= self._min_origin_coverage_ratio:
            reason = "coverage sufficient"
            need_generate = False
        else:
            reason = "coverage insufficient"
            need_generate = True

        return {
            "need_generate": need_generate,
            "origin_count": origin_count,
            "total_sentences": total_sentences,
            "coverage": coverage,
            "reason": reason
        }

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
            input_record_count = (
                len(self._search_record.get("search_record", []))
                if isinstance(self._search_record, dict) else 0
            )
            logger.info("[TRACE_DIAG] research_trace_source: search_record input count=%d", input_record_count)
            preprocessed_search_record = preprocess_search_record(self._search_record,
                                                                  self._search_record_max_content_len)
            if not preprocessed_search_record:
                logger.warning(
                    "[TRACE_DIAG] research_trace_source: preprocess_search_record "
                    "返回空, 退出溯源 (input count=%d)", input_record_count
                )
                return
            preprocessed_count = (
                len(preprocessed_search_record.get("search_record", []))
                if isinstance(preprocessed_search_record, dict) else 0
            )
            logger.info("[TRACE_DIAG] research_trace_source: preprocessed_search_record count=%d", preprocessed_count)
            if not LogManager.is_sensitive():
                logger.debug(
                    f"[research_trace_source] 预处理后的搜索记录: %s",
                    json.dumps(preprocessed_search_record, ensure_ascii=False, indent=2))

            # 识别需要引用的内容
            content_recognition_result = await recognize_content_to_cite(
                preprocessed_report, self._similarity_threshold, self._llm_model_name)
            if not content_recognition_result:
                logger.warning("[TRACE_DIAG] research_trace_source: recognize_content_to_cite 未识别到需要引用的内容")
                return
            logger.info(
                "[TRACE_DIAG] research_trace_source: content_recognition_result count=%d",
                len(content_recognition_result) if isinstance(content_recognition_result, (list, dict)) else 1
            )

            # 获取溯源匹配结果
            trace_results = await match_sources(
                content_recognition_result,
                preprocessed_search_record,
                self._chunk_size,
                self._llm_model_name
            )
            if not trace_results:
                logger.warning(
                    "[TRACE_DIAG] research_trace_source: match_sources "
                    "未获取到有效溯源结果 (input records=%d)", preprocessed_count
                )
                return
            logger.info(
                "[TRACE_DIAG] research_trace_source: match_sources trace_results count=%d",
                len(trace_results) if isinstance(trace_results, (list, dict)) else 1
            )

            # 生成溯源引用信息datas
            datas = generate_source_datas(preprocessed_report, preprocessed_search_record, trace_results)
            self._trace_source_datas = datas
            logger.info(
                "[TRACE_DIAG] research_trace_source: generate_source_datas count=%d",
                len(datas) if isinstance(datas, list) else 0
            )

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
            logger.info(
                "[TRACE_DIAG] add_source_to_report: trace_source_datas input count=%d",
                len(datas) if isinstance(datas, list) else 0
            )
            # 针对完整report，先进行预处理，删除文末参考文献章节
            removed_section, preprocessed_report = preprocess_report(self._report)

            # 提取原始报告中已经存在的引用内容
            origin_report_dict = generate_origin_report_data(
                preprocessed_report, self._classified_content)
            origin_report_datas = origin_report_dict.get("origin_report_data", [])
            need_add_source_report = origin_report_dict.get("modified_report", "")
            logger.info(
                "[TRACE_DIAG] add_source_to_report: origin_report_datas count=%d",
                len(origin_report_datas) if isinstance(origin_report_datas, list) else 0
            )

            # 对文中自带的引用内容和传入的引用信息列表做合并排序
            all_datas = merge_source_datas(
                need_add_source_report, datas, origin_report_datas)
            logger.info(
                "[TRACE_DIAG] add_source_to_report: merge_source_datas count=%d",
                len(all_datas) if isinstance(all_datas, list) else 0
            )

            # 使用datas列表，对原始报告文本进行来源引用添加
            added_source_report, all_datas = add_source_references(need_add_source_report, all_datas)
            added_source_report = added_source_report + removed_section
            logger.info(
                "[TRACE_DIAG] add_source_to_report: add_source_references final count=%d",
                len(all_datas) if isinstance(all_datas, list) else 0
            )

            if not LogManager.is_sensitive():
                logger.info(f'[add_source_to_report] 添加来源引用后的报告 {added_source_report}')
                logger.debug(f'[add_source_to_report] 合并后的引用信息: %s',
                             json.dumps(all_datas, ensure_ascii=False, indent=2))
            return dict(modified_report=added_source_report, datas=all_datas)
        except Exception as e:
            raise CustomValueException(StatusCode.SOURCE_TRACER_ADD_SOURCE_ERROR.code,
                                       StatusCode.SOURCE_TRACER_ADD_SOURCE_ERROR.errmsg.format(e=str(e))) from e
