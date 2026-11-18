# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
图表插入位识别模块

功能：
1. 对报告按一级标题进行划分
2. LLM识别可插入图表的内容
3. 生成占位符 @import:{图表名称}+figure_{id}
4. 生成信息收集任务
"""

import asyncio
import logging
import re
from typing import Dict, List, Any, Tuple

from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.algorithm.chart_generation.utils import call_model, CallModelInput
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

logger = logging.getLogger(__name__)

_H1_LINE_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_LINE_RE = re.compile(r"^##\s+(.+?)\s*$")
_FENCE_LINE_RE = re.compile(r"^\s*(```|~~~)")
_CHAPTER_TITLE_RE = re.compile(
    r"^(第\s*[0-9一二三四五六七八九十百千]+\s*[章节篇]"
    r"|[0-9]+\s*[.、)）]"
    r"|chapter\s+\d+"
    r"|part\s+\d+)",
    re.IGNORECASE,
)
_EXCLUDED_TITLES = frozenset(
    {
        "目录",
        "table of contents",
        "contents",
        "摘要",
        "abstract",
        "结论",
        "conclusion",
        "参考文章",
        "参考文献",
        "reference articles",
        "references",
    }
)


def _split_by_heading(content: str, pattern: "re.Pattern[str]") -> List[Dict[str, str]]:
    """按标题行切分 *content*，返回 ``[{"title", "content"}, ...]``。

    用正则 match 的偏移量切片，而不是 ``content.find(title)``：后者永远命中
    title 的第一次出现，遇到重复标题时后续章节的 content 会错位甚至为空。
    围栏代码块（``` / ~~~）内的行不参与切分，避免把 ``# 注释`` 当成标题。
    """
    blocks: List[Dict[str, str]] = []
    in_fence = False
    offset = 0
    current = None

    for line in content.splitlines(keepends=True):
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            match = pattern.match(line.rstrip("\r\n"))
            if match:
                if current is not None:
                    current["content"] = content[current.pop("_start"):offset].strip()
                current = {"title": match.group(1).strip(), "_start": offset + len(line)}
                blocks.append(current)
        offset += len(line)

    if current is not None:
        current["content"] = content[current.pop("_start"):].strip()

    return blocks


class FigurePlaceholderGenerator:
    """图表占位符生成器"""

    def __init__(self, llm_model_name: str):
        """
        初始化

        Args:
            llm_model_name: LLM模型名称
        """
        self._llm_model = llm_model_name
        self.figure_id_counter = 0

    async def run(self, report_content: str
                  ) -> Dict[int, List[Dict[str, Any]]]:
        """运行图表占位符生成器"""
        """
        1. 移除报告中的溯源信息
        2. 移除报告中的表格
        3. 按一级标题划分报告
        4. 以二级标题拆分一级章节
        5. 先预定位图片插入点，让图片选择预定位范围内的坐标进行插入
        6. 选出插入锚点，输出图表生成信息序列
        
        Args:
            report_content: 报告内容
        Returns:
            Dict[int, List[Dict[str, Any]]]: 按章节划分的图表生成信息序列
                - 图表描述
                - 图表类型
                - 信息收集任务序列
                - 插入锚点id
                - 图表所在二级章节内容
                - 图表所在一级章节索引
                - 图表前文
        
        """
        try:
            report_content_rm = self._remove_table(report_content)
            sections = self._split_report_by_h1(report_content_rm)
            gen_chart_tasks = await self.generate_chart_tasks(sections)
        except Exception as e:
            error_msg = f"[CHART GENERATION] Chart Task Generator Error: {repr(e)}"
            logger.error(error_msg)
            raise CustomValueException(StatusCode.CHART_PLACEHOLDER_ERROR.code,
                                      StatusCode.CHART_PLACEHOLDER_ERROR.errmsg.format(e=error_msg)) from e
        return gen_chart_tasks

    @staticmethod
    def _remove_table(report_content: str) -> str:
        """表格不参与生成图，移除报告中的表格"""
        # 匹配完整的markdown表格：表头、分隔符行、数据行
        table_pattern = re.compile(
            r"""
            ^\|.*\|$              # 表头行：以|开头和结尾
            \n                    # 换行
            ^\|[-:\s|]+\|$       # 分隔符行：包含-、:、|和空格
            \n                    # 换行
            (?:^\|.*\|$\n?)+     # 数据行：一个或多个以|开头和结尾的行
            """,
            re.MULTILINE | re.VERBOSE,
        )
        return table_pattern.sub("", report_content)

    @staticmethod
    def _split_report_by_h1(report_content: str) -> List[Dict[str, str]]:
        """
        按一级标题划分报告

        Args:
            report_content: 报告内容

        Returns:
            List[Dict[str, str]]: 划分后的报告块列表，每个包含:
                - index: 章节序号 (从1开始)
                - title: 一级标题
                - content: 章节内容
        """
        blocks = _split_by_heading(report_content, _H1_LINE_RE)
        if not blocks:
            # 没有一级标题，将整个报告作为一个块
            return [{"index": 1, "title": "全文", "content": report_content}]

        report_sections = []
        for position, section in enumerate(blocks):
            normalized_title = re.sub(r"\s+", " ", section["title"]).strip().casefold()
            if normalized_title in _EXCLUDED_TITLES:
                continue
            # 首个 H1 通常是报告标题（不带章节号）。若正文直接从"第一章 / 1."开始，
            # 说明报告没有总标题，此时不能按位置把它当标题丢掉。
            if position == 0 and not _CHAPTER_TITLE_RE.match(section["title"]):
                continue
            report_sections.append(section)

        for index, section in enumerate(report_sections, start=1):
            section["index"] = index

        logger.info(f"Split report into {len(report_sections)} sections by H1")
        return report_sections

    @staticmethod
    def _split_section_by_h2(section: Dict) -> List[Dict[str, str]]:
        """以二级标题拆分一级章节"""
        section_content = section.get("content", "")
        index = section.get("index", -1)
        
        blocks = _split_by_heading(section_content, _H2_LINE_RE)
        if not blocks:
            # 没有二级标题，将整个章节作为一个块
            return [
                {
                    "index": index,
                    "index_h2": 0,
                    "title": section.get("title", ""),
                    "content": section_content,
                }
            ]

        sub_sections = [
            {
                "index": index,
                "index_h2": position,
                "title": block["title"],
                "content": block["content"],
            }
            for position, block in enumerate(blocks)
        ]

        logger.info(f"Split section_{index} into {len(sub_sections)} sub_sections by H2")
        return sub_sections

    @staticmethod
    def _position_anchor(section: str) -> Tuple[str, Dict[str, str]]:
        """先预定位图片插入点，让图片选择预定位范围内的坐标进行插入"""
        section_paragraphs = section.split("\n")
        pos_char = "@import_chart_{index}"
        section_insert_anchor = []
        anchor_msg = {}
        for index, para in enumerate(section_paragraphs):
            if para:
                anchor_msg[index] = para # 锚点信息中的报告内容不包含占位符
                para += pos_char.format(index=index)
                section_insert_anchor.append(para)
        return ''.join(section_insert_anchor), anchor_msg

    async def generate_chart_tasks(
        self, sections: List[Dict[str, str]]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        识别可插入图表的位置并生成占位符

        Args:
            sections: 划分后的报告章节列表

        Returns:
            Dict[int, List[Dict[str, Any]]]:
                - 全文包含的一级章节索引
                   - 一级章节包含的二级章节序列
                       - 二级章节包含的图表生成信息序列
        """
        # 将一级章节划分为二级章节
        sub_sections_h1 = []
        for section in sections:
            # 每个一级章节包含多个二级章节
            sub_sections_h1.append(self._split_section_by_h2(section))
            
        # 并行处理每个一级章节
        tasks = []
        for sec_h1 in sub_sections_h1:
            task = self._process_section_h1(sec_h1)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        if len(results) != len(sections):
            # 一级标题结果数量与章节数量不符，将导致图表信息错位，抛出异常
            error_msg = f"Error processing sections_report: {len(results)} != {len(sections)}"
            logger.error(error_msg)
            raise CustomValueException(StatusCode.CHART_PLACEHOLDER_ERROR.code,
                                      StatusCode.CHART_PLACEHOLDER_ERROR.errmsg.format(e=error_msg))

        # 将一级章节索引添加到结果中
        section_inder_chart_tasks = {}
        for i, result in enumerate(results):
            if result:
                section_inder_chart_tasks[i + 1] = result

        return section_inder_chart_tasks

    async def _process_section_h1(self, sections: List[Dict[str, str]]
                                  ) -> List[Dict[str, Any]]:
        """
        异步处理一级章节，识别图表插入位

        Args:
            sections: 一级章节中包含的所有二级章节列表

        Returns:
            List[Dict[str, Any]]: 图表生成信息序列
        """
        tasks = []
        for section in sections:
            task = self._process_section_h2(section)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        if len(results) != len(sections):
            # 生成的结果章节数量不符不会导致异常，但需要记录日志
            logger.error(f"Error processing sections_h1: {len(results)} != {len(sections)}")
        h1_tasks = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("[CHART GENERATION] section task failed: %s", result)
                continue
            if isinstance(result, list):
                h1_tasks.extend(result)
            else:
                logger.warning("[CHART GENERATION] skip non-list result: %r (type=%s)", result, type(result).__name__)
        # 为每个一级章节下的图表生成唯一的图表id
        chart_id_in_section = 0
        for task in h1_tasks:
            chart_id_in_section += 1
            task["chart_id_in_section"] = chart_id_in_section
        
        return h1_tasks

    async def _process_section_h2(self, section: Dict[str, str]
                                  ) -> List[Dict[str, Any]]:
        """
        处理单个二级章节，识别图表插入位

        Args:
            section: 章节信息

        Returns:
            List[Dict[str,str,List[str],str,int]]:
                - 图表生成信息序列
                    - 图表描述
                    - 图表类型
                    - 信息收集任务序列
                    - 图表前文内容
                    - 图表所在章节索引
        """
        section_title = section["title"]
        section_content = section["content"]

        """图表修改： 这一步首先将section按照换行符划分成段，将段序列输入llm识别需要添加图表的段, 
        输出与段个数和顺序对应的json sheama：[图表描述/图表类型/数据收集任务集]，如果不生成图表则返回空占位序列"""
        section_with_anchor, sub_anchor_msg = self._position_anchor(section_content)

        try:
            call_model_input = CallModelInput(
                model_name=self._llm_model, 
                prompt="vlm_find_insert_point_prompt", 
                user_input={"section_contents": section_with_anchor}, 
                agent_name=AgentLlmName.VLM_CHART_GENERATOR_FIND_INSERT_POINT.value
                )
            response = await call_model(call_model_input)

            # 解析LLM响应, 将定位锚点和图表生成信息记录并返回
            return self._parse_llm_response(response, sub_anchor_msg, section)
        except Exception as e:
            logger.error(
                f"[CHART GENERATION] Error calling LLM for section {section_title}: {e}"
            )

    @staticmethod
    def _parse_llm_response(
        response: List,
        sub_anchor_msg: Dict,
        section: Dict,
    ) -> List[Dict[str, Any]]:
        """
        解析LLM响应，生成占位符和信息收集任务

        Args:
            response: LLM响应内容
            sub_anchor_msg: 当前处理的二级章节内容的图表锚点信息
            section: 二级章节信息

        Returns:
            List[Dict[str,str,List[str],str,int]]:
                - 图表生成信息序列
                    - 图表描述
                    - 图表类型
                    - 信息收集任务序列
                    - 图表前文内容
                    - 图表所在章节索引
        """
        gen_chart_tasks = []
        try:
            for res in response:
                if not isinstance(res, dict):
                    continue
                res["context"] = section.get("content", "")
                res["section_index"] = section.get("index", -1)
                placeholder_index = res.get("placeholder_index", -1)
                if placeholder_index == -1 or placeholder_index not in sub_anchor_msg:
                    continue
                res["anchor_match_para"] = sub_anchor_msg[placeholder_index]
                gen_chart_tasks.append(res)
        except Exception as e:
            logger.error(f"[CHART GENERATION] Error parsing LLM response: {e}")
            return []
        return gen_chart_tasks
