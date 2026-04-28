# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
图表数据收集模块

功能：
1. 根据 all_classified_contents 中的 index 划分不同报告章节的信息源
2. 根据图表需求收集相应数据
3. 将数据格式化为 {数据名称: 数据} 的格式
"""

import asyncio
import logging
from typing import Dict, List, Tuple, Any, Optional

from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.algorithm.chart_generation.utils import call_model, is_equal_length, CallModelInput
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

logger = logging.getLogger(__name__)


class ChartDataCollector:
    """图表数据收集器"""

    def __init__(self, llm_model_name: str):
        """
        初始化

        Args:
            llm_model_name: LLM模型名称
        """
        self._llm_name = llm_model_name
        self._batch_size = 3
        self._all_data_source_with_index = None  # 信息源
        self._meta_collect_tasks = None # 图表信息收集任务
        self._log_prefix = "[ChartDataCollector]"
        
    async def run(self, meta_collect_tasks: Dict[int, List[Dict[str, Any]]],
                  all_classified_contents: List[List[Dict[str, Any]]],
                  ) -> Dict[int, List[Dict[str, Any]]]:
        """运行图表数据收集器
        
        Args:
            meta_collect_tasks: 图表信息收集任务
            all_classified_contents: 初始信息源，序列元素表示每个一级章节依赖参考

        Returns:
            Dict[int, List[Dict[str, Any]]]: 汇总后的meta_collect_tasks
              - 章节索引
                - 任务列表
                  - description: 图表描述
                  - chart_type: 图表类型
                  - collection_tasks: 图表收集任务列表
                  - placeholder_index: 图表占位符索引
                  - context: 图表上下文
                  - section_index: 一级章节索引，从1开始
                  - anchor_match_para: 图表锚点匹配前文内容
                  - data: 图表数据
                  - source_titles: 数据来源序列的标题
                  - source_urls: 数据来源序列的URL
        """
        try:
            if not meta_collect_tasks:
                raise ValueError(f"{self._log_prefix} meta_collect_tasks is empty!")
            self._meta_collect_tasks = meta_collect_tasks
            # 按一级章节划分信息源
            self._classify_contents_by_index(all_classified_contents)
            # 按一级章节将任务分批
            collect_tasks = self._organize_collection_task()
            # 异步收集所有图表的数据
            all_chart_data = await self._collect_data_for_figures(collect_tasks)
        except Exception as e:
            error_msg = f"{self._log_prefix} Error running chart data collector: {e}"
            logger.error(error_msg)
            raise CustomValueException(StatusCode.CHART_DATA_COLLECTION_ERROR.code,
                                       StatusCode.CHART_DATA_COLLECTION_ERROR.errmsg.format(e=error_msg)) from e
        return all_chart_data

    def _classify_contents_by_index(
        self, all_classified_contents: List[Dict[str, Any]]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        根据index将all_classified_contents划分成不同报告章节的信息源

        Args:
            all_classified_contents: 报告生成用到的所有数据和内容列表

        Returns:
            Dict[int, List[Dict[str, Any]]]: 按章节index分组的信息源, index从1开始
        """
        classified_data = {}
        cur_section_index = 1

        for item in all_classified_contents:
            section_source = []
            for source in item:
                new_source = source.copy()
                section_source.append(new_source)
            classified_data[cur_section_index] = section_source
            cur_section_index += 1
        self._all_data_source_with_index = classified_data
        logger.info(
            f"{self._log_prefix} Classified  original contents into \
                {len(classified_data)} sections by index."
        )
        return classified_data

    def _organize_collection_task(self) -> Dict[int, Dict[str, List[Dict[str, Any]]]]:
        """
        将图表数据收集任务分批
        
        Returns:
            Dict[int, Dict[str, Any]]: 按一级章节索引分组的信息收集任务
                - collection_tasks: 信息收集任务序列
                - data_sources: 该章节的信息源
        """
        collect_tasks = {}
        for index, task_list_h1 in self._meta_collect_tasks.items():
            if index not in self._all_data_source_with_index:
                error_msg = f"{self._log_prefix} Section index {index} not found in classified data"
                logger.warning(error_msg)
                raise CustomValueException(StatusCode.CHART_DATA_COLLECTION_ERROR.code, 
                                           StatusCode.CHART_DATA_COLLECTION_ERROR.errmsg.format(e=error_msg))
            data_sources = []
            source_index_in_sec = 0
            for source in self._all_data_source_with_index[index]:
                data_sources.append({
                                     "content": source.get("original_content", ""),
                                     "index": source_index_in_sec
                                     })
                source_index_in_sec += 1
            cur_section_tasks = []
            for task in task_list_h1:
                cur_task = {}
                cur_task["collection_tasks"] = task.get("collection_tasks", [])
                cur_section_tasks.append(cur_task)
            collect_tasks[index] = {"collection_tasks": cur_section_tasks, "data_sources": data_sources}
                
        return collect_tasks

    async def _collect_data_for_figures(
        self,
        collect_tasks: Dict[int, Dict[str, List[Dict[str, Any]]]],
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        异步收集所有图表的数据

        Returns:
            Dict[str, Dict[str, Any]]: 图表数据字典 {图表ID: {数据名称: 数据}}
        """

        # 创建异步任务
        tasks = []
        
        # 异步执行每个一级章节下的图表收集任务
        for section_index, task_data in collect_tasks.items():
            tasks.append(self._collect_section_chart_data(task_data, section_index))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 汇总结果
        # 按章节划分
        all_chart_data = self._post_process_section_data(results)

        return all_chart_data

    def _post_process_section_data(self, 
                                   all_section_data: List[List[Dict[str, Any]]]
                                   ) -> Dict[int, List[Dict[str, Any]]]:
        """将所有章节数据分别汇总到各自的meta_collect_tasks中
        
        Args:
            all_section_data: 收集得到的所有章节下图表收集任务对应的数据

        Returns:
            Dict[int, List[Dict[str, Any]]]: 汇总后的meta_collect_tasks
              - 章节索引
                - 任务列表
                  - description: 图表描述
                  - chart_type: 图表类型
                  - collection_tasks: 图表收集任务列表
                  - placeholder_index: 图表占位符索引
                  - context: 图表上下文
                  - section_index: 一级章节索引，从1开始
                  - anchor_match_para: 图表锚点匹配前文内容
                  - data: 图表数据
                  - source_datas: 数据来源序列
        """
        try:
            if len(all_section_data) != len(self._meta_collect_tasks):
                raise ValueError(f"Section data length ({len(all_section_data)}) !=" \
                                 f"meta tasks length ({len(self._meta_collect_tasks)})")
            meta_chart_infos = {}
            for (section_index, meta_infos), section_datas in zip(self._meta_collect_tasks.items(), all_section_data):
                if not section_datas:
                    logger.warning(f"No data for section {section_index}")
                    continue
                meta_chart_infos[section_index] = meta_infos.copy()
                for task_index, section_data in enumerate(section_datas):
                    # 没有收集到对应数据的图表生成任务，跳过
                    if not section_data:
                        continue
                    meta_chart_infos[section_index][task_index].update(section_data)
        except Exception as e:
            error_msg = f"{self._log_prefix} Error post processing section data: {e}"
            logger.error(error_msg)
            raise CustomValueException(StatusCode.CHART_DATA_COLLECTION_ERROR.code,
                                       StatusCode.CHART_DATA_COLLECTION_ERROR.errmsg.format(e=error_msg)) from e
        return meta_chart_infos
            
        

    async def _collect_section_chart_data(
        self, section_collect_tasks: Dict[str, List[Dict[str, Any]]], 
        section_index: int
    ) -> List[Dict[str, Any]]:
        """
        收集单个章节下的所有图表的数据

        Args:
            section_collect_tasks: 该章节的信息收集任务序列
            section_index: 章节索引

        Returns:
            List[Dict[str, Any]]: 单个章节的数据列表
        """
        
        # 任务分批
        cur_section_tasks = section_collect_tasks.get("collection_tasks", [])
        data_sources = section_collect_tasks.get("data_sources", [])
        if not cur_section_tasks:
            logger.warning(f"No collect tasks for section {section_index}")
            return []
        if not data_sources:
            logger.warning(f"No data sources for section {section_index}")
            return []
        # 同一章节下的任务共享同一信息源
        section_tasks_batch = [cur_section_tasks[i:i + self._batch_size] 
                               for i in range(0, len(cur_section_tasks), self._batch_size)]
        
        tasks = []
        for batch_tasks in section_tasks_batch:
            tasks.append(self._collect_batch_tasks(batch_tasks, data_sources))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 将数据源溯源信息添加到结果中
        section_datas = []
        for result in results:
            section_datas.extend(self._add_section_source_info(result, section_index))

        if len(section_datas) != len(cur_section_tasks):
            error_msg = f"{self._log_prefix} Number of section data {len(section_datas)} " \
                        f"does not match number of section tasks {len(cur_section_tasks)}"
            logger.error(error_msg)
            return [{}] * len(cur_section_tasks)

        return section_datas

    def _add_section_source_info(self, 
                                 result: List[Dict[str, Any]], 
                                 section_index: int) -> List[Dict[str, Any]]:
        """
        将数据源溯源信息添加到结果中

        Args:
            result: 批次收集的数据列表
            section_index: 章节索引
        """
        section_data = []
        data_sources = self._all_data_source_with_index[section_index]
        for result_item in result:
            try:
                if "NO DATA" in result_item.get("data", "NO DATA"):
                    section_data.append({})
                    continue
            
                if not result_item.get("source_indexes", []):
                    section_data.append({})
                    continue
                source_indexes = result_item.get("source_indexes", [])
                new_result_item = {}
                new_result_item["source_datas"] = []
                new_result_item["data"] = result_item.get("data", "NO DATA")
                for source_index in source_indexes:
                    data_source = data_sources[source_index]
                    new_result_item["source_datas"].append(data_source) # 保存相应溯源的所有信息
                section_data.append(new_result_item)
            except Exception as e:
                logger.warning(f"{self._log_prefix} Error adding section source info: {e}")
                section_data.append({})
        return section_data

    async def _collect_batch_tasks(self, batch_tasks: Dict[str, List[Dict[str, Any]]], 
                                   data_sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        收集一批任务的数据

        Args:
            batch_tasks: 一批任务
            data_sources: 信息源数据

        Returns:
            List[Dict[str, Any]]: 批次收集的数据列表
        """
        
        try:
            detection_func_and_args = {
                "detection_func": is_equal_length,
                "args": len(batch_tasks)
            }
            call_model_input = CallModelInput(
                model_name=self._llm_name, 
                prompt="vlm_collect_data_prompt", 
                user_input={"tasks": batch_tasks, "data_sources": data_sources},
                agent_name=AgentLlmName.VLM_CHART_GENERATOR_COLLECT_DATA_FOR_CHART.value
                )
            results = await call_model(call_model_input, 
                                       detection_func_and_args=detection_func_and_args)
            if not results:
                raise ValueError(f"No results for batch tasks: {batch_tasks}")
        except Exception as e:
            logger.warning(f"{self._log_prefix} Error collecting data for batch tasks: {e}")
            return [{} for _ in range(len(batch_tasks))]
        return results
