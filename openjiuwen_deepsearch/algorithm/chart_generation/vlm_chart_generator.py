# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
图表生成节点

在报告生成之后独立调用的节点，执行以下步骤：
1. 图表插入位识别 - 对报告按一级标题划分，LLM识别可插入图表的内容
2. 信息收集和处理 - 从 all_classified_contents 获取数据
3. 图表生成 - LLM生成Python代码，绘图工具生成图表
4. 图表插入 - 将图表插入报告，返回修改后的报告和图片base64
"""
import asyncio
import logging
import json
import copy
from typing import Dict, List, Any, Optional, Tuple

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.algorithm.chart_generation.figure_placeholders import (
    FigurePlaceholderGenerator,
)
from openjiuwen_deepsearch.algorithm.chart_generation.data_collector import (
    ChartDataCollector,
)
from openjiuwen_deepsearch.algorithm.chart_generation.chart_generator import (
    ChartGenerator,
)
from openjiuwen_deepsearch.algorithm.chart_generation.insert_chart import (
    InsertChartNode,
)

logger = logging.getLogger(__name__)
OUTPUT_DIR = "./output/vlm_chart_generator"


class VLMChartGenerator:
    """
    VLM图表生成器

    Args:
        llm_model_name: LLM模型名称
        vlm_model_name: VLM模型名称
        use_vlm_critic: 是否使用VLM评估
    """
    def __init__(self, llm_model_name: str, vlm_model_name: str, 
                 vlm_max_iterations: int):
        self._llm_model_name = llm_model_name
        self._vlm_model_name = vlm_model_name
        self._vlm_max_iterations = vlm_max_iterations
        self._output_dir = OUTPUT_DIR
        self._source_trace_datas = None
        self._report_content = None
        self._all_classified_contents = None
        self._log_prefix = "[VLMChartGenerator]"
        
        self._figure_placeholder_generator = FigurePlaceholderGenerator(llm_model_name)
        self._chart_data_collector = ChartDataCollector(llm_model_name)
        self._chart_generator = ChartGenerator(llm_model_name=llm_model_name, 
                                               vlm_model_name=vlm_model_name, 
                                               vlm_max_iterations=vlm_max_iterations, 
                                               output_dir=self._output_dir
                                               )
        self._insert_chart_node = InsertChartNode(self._output_dir)
        
    def _check_input(self, report_content: str, 
                     all_classified_contents: List[Dict[str, Any]],
                     source_trace_datas: List[Dict[str, Any]]):
        """
        检查输入参数
        """
        if not report_content:
            error_msg = "报告内容不能为空"
            logger.error(f"{self._log_prefix} {error_msg}")
            raise CustomValueException(StatusCode.CHART_GENERATION_ERROR.code,
                                       StatusCode.CHART_GENERATION_ERROR.errmsg.format(e=error_msg))
        if not all_classified_contents:
            error_msg = "信息来源不能为空"
            logger.error(f"{self._log_prefix} {error_msg}")
            raise CustomValueException(StatusCode.CHART_GENERATION_ERROR.code,
                                       StatusCode.CHART_GENERATION_ERROR.errmsg.format(e=error_msg))
        if not source_trace_datas:
            warning_msg = "溯源信息为空"
            logger.warning(f"{self._log_prefix} {warning_msg}")

        self._report_content = report_content
        self._all_classified_contents = copy.deepcopy(all_classified_contents)
        self._source_trace_datas = copy.deepcopy(source_trace_datas)

    async def run(self, report_content: str, 
            all_classified_contents: List[Dict[str, Any]],
            source_trace_datas: List[Dict[str, Any]],
            ) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]]]:
        """
        运行图表生成流程
        
        Args:
            report_content: 报告内容
            all_classified_contents: 信息来源
            source_trace_datas: 溯源信息
            
        Returns:
            Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]]]: 
                chart_messages: vlm生成图模块的接口字段信息
                modified_report: 修改后的报告内容
                new_source_trace_datas: 新的溯源信息
        """
        
        try:
            self._check_input(report_content, all_classified_contents, source_trace_datas)
            # 选定图表插入锚点
            chart_data_collect_tasks = await self._figure_placeholder_generator.run(self._report_content)
            logger.debug("%s The chart data collection tasks: \n%s", self._log_prefix,
                         json.dumps(chart_data_collect_tasks, ensure_ascii=False, indent=2))
            logger.info(f"{self._log_prefix} Finish Selection of chart insert anchor points.")
            # 收集图表数据
            chart_genterate_tasks = await self._chart_data_collector.run(chart_data_collect_tasks, 
                                                              self._all_classified_contents)
            logger.debug("%s The collected chart data: \n%s", self._log_prefix,
                json.dumps(chart_genterate_tasks, ensure_ascii=False, indent=2))
            logger.info(f"{self._log_prefix} Finish Collection of chart data.")
            # vlm迭代生成图表
            chart_insert_tasks = await self._chart_generator.generate_charts(chart_genterate_tasks)
            logger.debug("%s The chart insert tasks: \n%s", self._log_prefix,
                json.dumps(chart_insert_tasks, ensure_ascii=False, indent=2))
            logger.info(f"{self._log_prefix} Finish Generation of chart.")
            # 插入图表
            modified_report, new_source_trace_datas = self._insert_chart_node.run(self._report_content, 
                                                         chart_insert_tasks, 
                                                         self._source_trace_datas)
            logger.debug("%s The modified report: \n%s", self._log_prefix, modified_report)
            logger.info(f"{self._log_prefix} Finish Insertion of chart into report.")
            # 输出接口字段构建
            chart_messages = self._build_output_interface(chart_insert_tasks)
            logger.debug("%s The output interface: \n%s", self._log_prefix,
                json.dumps(chart_messages, ensure_ascii=False, indent=2))
            logger.info(f"{self._log_prefix} Finish Building of output interface fields.")
            
            return chart_messages, modified_report, new_source_trace_datas
        except Exception:
            logger.error(f"{self._log_prefix} Error occurred during chart generation node running")
            raise

    @staticmethod
    def _build_output_interface(chart_insert_tasks: Dict[str, List[Dict[str, Any]]]
                                ) -> List[Dict[str, Any]]:
        """
        构建输出接口字段

        Args:
            chart_insert_tasks: 图表插入任务

        Returns:
            List[Dict[str, Any]]: 输出接口字段
        """
        chart_messages = []
        for _, charts in chart_insert_tasks.items():
            for chart in charts:
                chart_messages.append({
                    "chart_id": chart.get("chart_id", ""),
                    "chart_title": chart.get("chart_title", ""),
                    "description": chart.get("description", ""),
                    "base64": chart.get("chart_base64", "")
                })
        return chart_messages