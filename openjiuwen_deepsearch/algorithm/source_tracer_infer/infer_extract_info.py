# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
import logging
from typing import List, Dict
import asyncio

from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import call_model, type_check
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


class ResearchInferPreprocess():
    def __init__(self, context: Dict):
        self.language = context.get("language", "zh-CN")
        self.llm_model = context.get("llm_model_name", "")
        self.response = context.get("source_tracer_response", "")  # 获得经过溯源模块处理后的报告
        self.search_records = context.get("all_classified_contents", [])
        self.search_record_with_index = {}
        self.citation_pattern = r'\[\[([^\]]+)\]\]\([^)]+\)'
        self.conclusion_infos = {}

    async def run(self) -> List[Dict]:
        """预处理是为溯源推理提取报告里需要推理的结论以及对应的搜索记录"""
        logger.info(f"[INFERENCE INFO EXTRACT] Extracting preprocessed information for infer of research.")
        # 组织搜索记录
        self.classify_search_record()
        # 提取结论
        self.conclusion_infos = await self._extract_conclusion()
        if not self.conclusion_infos:
            logger.warning(f"[INFERENCE INFO EXTRACT] no conclusion extracted from report.")
            return []
        # 结论与搜索记录按章节配对
        conclusion_with_records = self._match_conclusion_with_records()
        if LogManager.is_sensitive():
            logger.info(f"[INFERENCE INFO EXTRACT] 结论与搜索记录匹配结果 ***")
        else:
            logger.info(f"[INFERENCE INFO EXTRACT] 结论与搜索记录匹配结果 {conclusion_with_records}")
        return conclusion_with_records

    def classify_search_record(self):
        """章节索引映射章节搜索记录， 修改成员变量 search_records"""
        search_record_with_index = {}
        for i, section_search_record in enumerate(self.search_records):
            search_record_with_index[i] = []
            for record in section_search_record:
                search_record_with_index[i].append({"title": record.get("title", ""),
                                                    "url": record.get("url", ""),
                                                    "content": record.get("original_content", "")
                                                    })
        self.search_record_with_index = search_record_with_index

    async def _extract_conclusion(self):
        """提取research模式的结论"""
        conclusions_info = await self._find_sentences_with_positions()
        if not conclusions_info:
            logger.warning(f"[INFERENCE INFO EXTRACT]: no conclusion extracted from report.")
        if LogManager.is_sensitive():
            logger.warning(f"[INFERENCE INFO EXTRACT]: 结论信息 ***")
        else:
            logger.warning(f"[INFERENCE INFO EXTRACT]: 结论信息 {conclusions_info}")
        return conclusions_info

    async def _find_sentences_with_positions(self) -> Dict[str, Dict]:
        """
        查找句子并记录精确位置信息

        Returns:
            字典结构：{
                '句子内容': {
                    "sentence_section_index", 章节索引,
                    'start_pos': global_start, 结论在全文中的起始位置
                    'end_pos': global_end, 结论在全文中的结束位置
                    'content_without_citation', 章节内容（去除行内引用）
                    'found': True
                }
            }
        """
        # 首先分割Markdown为章节
        sections = self._split_markdown_with_detailed_positions()
        results = {}

        # 排除不提取结论的章节 # 0: 标题, 1: 摘要 -1: 参考文献章节索引
        sections = sections[2:-1]

        # 从每个段落提取1个结论
        conclusions = await self._extract_conclusions_for_sections(sections)

        # 定位结论在章节中的位置
        for section_index, conclusion in enumerate(conclusions):
            for sentence in conclusion:
                sentence = sentence.strip()
                if not sentence:
                    continue

                results[sentence] = self._locate_sentence_in_sections(sentence, sections[section_index], section_index)

        return results

    def _split_markdown_with_detailed_positions(self) -> List[Dict]:
        """
        分割Markdown并记录详细位置信息
        """
        h1_pattern = r'(?m)^#\s+(.+)$'
        h1_matches = list(re.finditer(h1_pattern, self.response))

        sections = []

        # 处理各个章节
        for i, match in enumerate(h1_matches):
            title = match.group(1).strip()
            content_start = match.end()

            if i < len(h1_matches) - 1:
                content_end = h1_matches[i + 1].start()
            else:
                content_end = len(self.response)

            content = self.response[content_start:content_end]

            sections.append({
                'content': content,
                'global_start': content_start,
                'global_end': content_end,
            })

        return sections

    async def _extract_conclusions_for_sections(self, sections):
        """从每个章节中提取1个推理结论"""
        logger.info(f"[INFERENCE INFO EXTRACT] extract_conclusions starting...")

        detection_func_and_args = {"detection_func": type_check, "args": list}
        tasks = [call_model(self.llm_model, "infer_extract_conclusion_prompt", {"input": section.get("content", "")}, 
                            detection_func_and_args=detection_func_and_args, 
                            agent_name=NodeId.SOURCE_TRACER_INFER.value + 
                            "_extract_conclusion") for section in sections]
        results = await asyncio.gather(*tasks)
        if LogManager.is_sensitive():
            logger.info(f"[INFERENCE INFO EXTRACT]: Extracted conclusions: *")
        else:
            logger.info(f"[INFERENCE INFO EXTRACT]: Extracted conclusions: {results}")
        return results

    def _locate_sentence_in_sections(self, sentence: str, section: Dict, index) -> Dict:
        """
        在章节中定位句子的精确位置
        Args:
            sentence: 结论句子
            section: 提取结论的章节
            index: 章节索引
        Returns:
            dict={
                sentence_section_index:  章节索引
                start_pos: 结论在全文中的首位置
                end_pos: 结论在全文中的末位置
                content_without_citation: 清理掉引用信息的章节文本
                found: 是否在section中找到sentence
            }
        """
        # 在章节内容中查找句子
        content = section['content']
        sentence_start = content.find(sentence)

        if sentence_start != -1:
            # 计算在完整文本中的全局位置
            global_start = section['global_start'] + sentence_start
            global_end = global_start + len(sentence)

            return {
                "sentence_section_index": index,  # 返回章节索引，考虑前两个标题为非正文内容(标题和摘要)
                'start_pos': global_start,
                'end_pos': global_end,
                'content_without_citation': self._clean_citation(content),
                'found': True
            }
        else:

            return {
                'sentence_section_index': -1,
                'start_pos': -1,
                'end_pos': -1,
                'found': False
            }

    def _clean_citation(self, content: str) -> str:
        """
        清理行内引用
        """
        cleaned_content = re.sub(self.citation_pattern, '', content)
        return cleaned_content

    def _match_conclusion_with_records(self) -> List[Dict]:
        """按章节匹配结论与搜索记录"""
        match_conclusion_with_records = []
        # 按位置从后往前处理，避免位置偏移
        sorted_sentences = sorted(
            [(sentence, info) for sentence, info in self.conclusion_infos.items() if info['found']],
            key=lambda x: x[1]['start_pos'],
            reverse=True  # 从后往前处理
        )

        # 准备结论章节的搜索记录
        all_search_record = []
        for _, record in self.search_record_with_index.items():
            all_search_record += record

        for conclusion, info in sorted_sentences:
            section_idx = info.get("sentence_section_index", None)
            section_content = info.get("content_without_citation", "")
            if section_idx is not None and section_idx in self.search_record_with_index:
                match_conclusion_with_records.append({
                    "conclusion": [section_content, conclusion],  # 章节内容 + 结论
                    "search_records": self.search_record_with_index[section_idx],  # 章节对应的搜索记录 + programmer info
                    "start_pos": info['start_pos'],
                    "end_pos": info['end_pos']
                })
            elif section_idx not in self.search_record_with_index:
                # 添加结论章节
                match_conclusion_with_records.append({
                    "conclusion": [section_content, conclusion],  # 结论章节 + 提取的结论
                    "search_records": all_search_record,
                    "start_pos": info['start_pos'],
                    "end_pos": info['end_pos']
                })

        return match_conclusion_with_records
