# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
图表生成模块

功能：
1. 根据图表名称和生成数据，LLM生成Python代码
2. 执行代码生成图表
3. 可选的VLM迭代反馈机制
"""

import asyncio
import logging
import os
import re
import json
import textwrap
from typing import Dict, List, Tuple, Optional, Any

from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.algorithm.chart_generation.utils import (
    call_model,
    CallModelInput,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.algorithm.chart_generation.sandbox import (
    AsyncCodeExecutor,
)

logger = logging.getLogger(__name__)

# Use __file__ for robust path resolution in SDK mode
FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "kt_font.ttf")
# 并发控制：限制同时执行的沙箱子进程数量，避免内存耗尽和进程阻塞
MAX_CONCURRENT_CHART_TASKS = 5

CHART_THRESHOLD = 85


class ChartGenerator:
    """图表生成器"""

    def __init__(
        self,
        llm_model_name: str,
        output_dir: str,
        vlm_model_name: Optional[str] = None,
        vlm_max_iterations: int = 1,
    ):
        """
        初始化

        Args:
            llm_model_name: LLM模型名称（用于生成代码）
            output_dir: 图表输出目录
            vlm_model_name: VLM模型名称（用于评估反馈），None表示不使用
        """
        self._llm_model = llm_model_name
        self._vlm_model = vlm_model_name
        self.output_dir = output_dir
        self._vlm_max_iterations = vlm_max_iterations
        self._chart_threshold = CHART_THRESHOLD
        self._log_prefix = "[ChartGenerator]"
        # 最大并发图表生成任务数，限制子进程数量
        self._max_concurrent_tasks = MAX_CONCURRENT_CHART_TASKS

        if self._vlm_max_iterations > 0 and not self._vlm_model:
            error_msg = "使用VLM评估时，必须提供VLM模型名称"
            logger.error(f"{self._log_prefix} {error_msg}")
            raise CustomValueException(
                StatusCode.CHART_VLM_GENERATION_ERROR.code,
                StatusCode.CHART_VLM_GENERATION_ERROR.errmsg.format(e=error_msg),
            )

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

    def _create_code_executor(self) -> AsyncCodeExecutor:
        """为单个图表任务创建独立沙箱执行器，避免并发任务共享全局变量。"""
        code_executor = AsyncCodeExecutor(working_dir=self.output_dir, exec_timeout=120)
        return code_executor

    async def generate_charts(
        self,
        chart_tasks: Dict[int, List[Dict[str, Any]]],
    ) -> Dict[str, str]:
        """
        批量生成图表

        Args:
            chart_tasks: 图表生成任务列表
            use_vlm_critic: 是否使用VLM评估反馈

        Returns:
            Dict[str, str]: 图表各项信息
        """
        # 并行每个章节的图表生成任务
        section_coroutines: List[asyncio.Future] = []
        section_indices: List[int] = []
        try:
            if not chart_tasks:
                raise ValueError(f"{self._log_prefix} chart_tasks is empty!")
            for section_idx, section_tasks in chart_tasks.items():
                section_indices.append(section_idx)
                section_coroutines.append(
                    self._generate_section_charts(section_tasks, section_idx)
                )

            results = await asyncio.gather(*section_coroutines, return_exceptions=True)

            # 结果后处理
            report_chart_results = self._post_process_report_results(
                results, section_indices
            )
            return report_chart_results
        except Exception as e:
            error_msg = f"Error generating charts: {e}"
            logger.error(error_msg)
            raise CustomValueException(
                StatusCode.CHART_VLM_GENERATION_ERROR.code,
                StatusCode.CHART_VLM_GENERATION_ERROR.errmsg.format(e=error_msg),
            ) from e

    @staticmethod
    def _post_process_report_results(
        results: List[List[Dict[str, Any]]], chart_tasks_section_idx: List[int]
    ) -> List[Dict[int, Dict[str, Any]]]:
        """后处理报告图表生成结果，将生成图表对应到各自的章节"""
        try:
            if len(results) != len(chart_tasks_section_idx):
                raise ValueError(
                    f"Results length ({len(results)}) != "
                    f"section indices length ({len(chart_tasks_section_idx)})"
                )
            report_chart_results = {}
            for result, section_idx in zip(results, chart_tasks_section_idx):
                report_chart_results[section_idx] = result
            return report_chart_results
        except Exception as e:
            error_msg = f"Error post processing report results: {e}"
            logger.error(error_msg)
            return {}

    async def _generate_section_charts(
        self, section_chart_tasks: List[Dict[str, Any]], section_idx: int
    ) -> List[Dict[str, Any]]:
        """
        生成同一章节中的图表，使用并发控制限制同时执行的沙箱进程数
        """

        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(self._max_concurrent_tasks)

        async def _generate_with_semaphore(chart_task: Dict[str, Any]):
            """带并发控制的图表生成"""
            async with semaphore:
                return await self._generate_single_chart(chart_task)

        # 异步生成每一个图表（带并发控制）
        tasks = []
        for chart_task in section_chart_tasks:
            tasks.append(_generate_with_semaphore(chart_task))

        # 等待所有图表生成完成
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 结果后处理
        section_chart_results = self._post_process_section_results(
            results, section_chart_tasks, section_idx
        )
        return section_chart_results

    @staticmethod
    def _post_process_section_results(
        results: List[Dict[str, Any]],
        section_chart_tasks: List[Dict[str, Any]],
        section_idx: int,
    ) -> List[Dict[str, Any]]:
        """后处理章节图表生成结果，将生成图表对应到任务"""
        try:
            section_chart_results = []
            if len(results) != len(section_chart_tasks):
                raise ValueError(
                    f"Results length ({len(results)}) != chart tasks length ({len(section_chart_tasks)})"
                )
            section_chart_idx = 1
            for result, chart_task in zip(results, section_chart_tasks):
                if result:
                    # result = {chart_base64, score}
                    section_chart_results.append(chart_task.copy())
                    section_chart_results[-1]["chart_base64"] = result.get("chart_base64", "")
                    section_chart_results[-1]["score"] = result.get("score", 0)
                    section_chart_results[-1]["chart_id"] = (
                        f"chart_{section_idx}_{section_chart_idx}"
                    )
                    section_chart_idx += 1
            return section_chart_results
        except Exception as e:
            logger.error(f"Error post processing section results: {e}")
            return []

    async def _generate_single_chart(self, chart_task: Dict[str, Any]
                                     ) -> Dict[str, Any]:
        """
        生成单个图表的代码并执行，进行VLM评估反馈（可选）

        Args:
            chart_task: 图表生成任务

        Returns:
            Dict[str, Any]: 
                chart_base64: 图表base64编码，失败返回None，过滤掉分数低于阈值的图表
                score: 图表分数（vlm迭代优化功能开启后该分数才有意义）
        """

        try:
            # 获取图表生成任务信息
            chart_data = chart_task.get("data", {})
            # 没有数据，无法生成图表，直接返回None
            if not chart_data:
                logger.warning(f"No data for chart: {chart_task.get('chart_id', '')}")
                return None

            chart_title = chart_task.get("chart_title", "")
            chart_description = chart_task.get("description", "")
            chart_type = chart_task.get("chart_type", "")

            figure_id = f"figure_{chart_task.get('section_index', -1)}_{chart_task.get('chart_id_in_section', -1)}"

            gen_chart_input = {
                "chart_title": chart_title,
                "chart_description": chart_description,
                "chart_type": chart_type,
                "chart_data": chart_data,
                "font_path": FONT_PATH,
                "history_messages": {},
            }
            suggestion_list = []

            # ---------- Part 1: Generate code and execute code ----------
            result = await self._generate_and_execute_code(gen_chart_input, figure_id)
            if not result or not result.get("chart_base64"):
                logger.warning(f"Failed to generate chart for {figure_id}")
                return {}
            code = result.get("code", "")
            chart_base64 = result.get("chart_base64", "")

            if self._vlm_max_iterations == 0:
                return {"chart_base64": chart_base64, "score": 0}

            # ---------- Part 2: VLM评估反馈（可选） ----------
            iterate = 0
            while iterate <= self._vlm_max_iterations:
                suggestion_and_score = await self._vlm_iterate(
                    chart_base64, gen_chart_input, suggestion_list
                )
                
                score = suggestion_and_score.get("score", 0)
                suggestions = suggestion_and_score.get("suggestion", "")
                iterate += 1
                
                if score >= self._chart_threshold:
                    logger.info(f"Chart generated successfully: {figure_id},"
                                f"chart title: {chart_title}, score: {score}")
                    final_base64 = chart_base64
                    # 释放内存
                    del chart_base64
                    del suggestion_list
                    del result
                    return {"chart_base64": final_base64, "score": score}
                elif iterate > self._vlm_max_iterations:
                    # 达到最大迭代优化次数，图分数没有达到输出阈值，返回空
                    logger.debug("Chart generated fail: %s, chart title: %s, score: %s",
                                 figure_id, chart_title, score)
                    return {}
                else:
                    logger.debug("Chart optimization suggestions: %s, chart title: %s, suggestions: %s",
                                 figure_id, chart_title, suggestions)
                    suggestion_list.append(suggestions)

                    gen_chart_input["history_messages"] = {
                        "code": code,
                        "error_msg": None,
                        "suggestion": [suggestions],
                    }

                    # ---------- Part 3: Generate code and execute code again ----------
                    # 释放旧的base64数据
                    old_base64 = chart_base64
                    del old_base64

                    result = await self._generate_and_execute_code(
                        gen_chart_input, figure_id
                    )
                    if not result or not result.get("chart_base64"):
                        logger.warning(f"Failed to generate chart for {figure_id}")
                        return {}
                    code = result.get("code", "")
                    chart_base64 = result.get("chart_base64", "")

        except Exception as e:
            logger.warning(f"Error generating chart: {e}")
            return {}

        return {}

    async def _generate_and_execute_code(
        self, gen_chart_input: Dict[str, Any], figure_id: str
    ) -> Dict[str, str]:
        """
        生成单个图表

        Args:
            gen_chart_input: 图表生成任务输入信息
            figure_id: 图表ID
        Returns:
            Dict[str, str]: {
                "code": 图表代码,
                "chart_base64": 图表base64编码
            }
        """

        # 为当前图表任务创建独立执行器，避免并发任务之间变量污染
        code_executor = self._create_code_executor()

        for _ in range(3):  # 最多迭代3次
            # 先取上一次的suggestion，用于后续的提示
            pre_suggestion = gen_chart_input.get("history_messages", {}).get(
                "suggestion", []
            )

            # 第1步：生成代码
            code = await self._generate_chart_code(gen_chart_input)
            if not code:
                # 没有生成代码，无法向下执行，本次任务失败
                logger.warning(f"Failed to generate code for {figure_id}")
                return {}

            # 第2步：执行代码
            # 在沙箱中执行代码
            result = await code_executor.execute(code)
            if result["error"]:
                logger.debug("Error executing chart code: %s", result['stderr'])
                gen_chart_input["history_messages"] = {
                    "code": code,
                    "error_msg": f"stdout: {result['stdout']}\nstderr: {result['stderr']}",
                    "suggestion": (
                        pre_suggestion if isinstance(pre_suggestion, list) else []
                    )
                    + [""],
                }
                continue

            # 第3步：获取图表base64
            chart_base64 = result.get("chart_base64")
            if chart_base64:
                logger.info(f"Chart generated successfully in memory for {figure_id}")
                return {"code": code, "chart_base64": chart_base64}
            else:
                logger.warning(f"No chart generated in code execution for {figure_id}")
                gen_chart_input["history_messages"] = {
                    "code": code,
                    "error_msg": None,
                    "suggestion": (
                        pre_suggestion if isinstance(pre_suggestion, list) else []
                    )
                    + [
                        "\nThe chart was not generated. Please ensure you create a matplotlib figure."
                    ],
                }
                continue
        return {}

    async def _generate_chart_code(
        self, gen_chart_input: Dict[str, Any]
    ) -> Optional[str]:
        """
        生成图表代码

        Args:
            单个图表生成任务的输入信息字典

        Returns:
            Optional[str]: 生成的Python代码
        """
        try:
            detect_func_and_args = {
                "detection_func": None,
                "args": {},
                "option": "skip normalize",
            }
            call_model_input = CallModelInput(
                model_name=self._llm_model,
                prompt="vlm_generate_chart_code_prompt",
                user_input=gen_chart_input,
                agent_name=AgentLlmName.VLM_CHART_GENERATOR_GENERATE_CHART_CODE.value,
            )
            response = await call_model(
                call_model_input, detection_func_and_args=detect_func_and_args
            )

            def extract_code(response: str) -> Optional[str]:
                """
                从LLM响应中提取Python代码
                优先提取 ```python 和 ``` 之间的内容；
                若末尾没有 ```，则提取 ```python 之后的全部内容。
                """
                # 先尝试匹配 ```python ... ``` 之间的内容
                match = re.search(r"```(?:python)?\s*([\s\S]*?)\s*```", response)
                if match:
                    return match.group(1).strip()
                # 若没有闭合的 ```，则提取 ```python 之后的全部内容
                match = re.search(r"```(?:python)?\s*([\s\S]*)", response)
                if match:
                    return match.group(1).strip()
                # 如果没有代码块标记，直接返回整个响应（可能是纯代码）
                return response.strip()

            # 提取代码
            code = extract_code(response)
            # 代码规范化
            return self._normalize_code(code)

        except Exception as e:
            logger.error(f"Error generating chart code: {e}")
            return None

    @staticmethod
    def _normalize_code(code: str) -> str:
        """
        代码规范化

        Args:
            code: Python代码
        """
        if code is None:
            return ""

        s = code.strip()

        # 1) 若上游把整段代码包成 JSON 字符串（最外层带引号），这里仅做一次解包。
        #    注意：不做手工 \\n / \\" 替换，避免误改代码里的字符串字面量内容。
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            try:
                loaded = json.loads(s)
                if isinstance(loaded, str):
                    s = loaded
            except Exception as e:
                logger.debug("Code is not a JSON string, using original: %s", str(e))

        # 2) 统一换行符为 \n
        s = s.replace("\r\n", "\n").replace("\r", "\n")

        # 3) 规范缩进/空白：整体去公共缩进；行首 tab 统一成 4 空格；去掉行尾空白
        s = textwrap.dedent(s)
        normalized_lines = []
        for line in s.split("\n"):
            line = re.sub(r"^\t+", lambda m: " " * 4 * len(m.group(0)), line)
            normalized_lines.append(line.rstrip())
        return "\n".join(normalized_lines).strip()

    async def _vlm_iterate(
        self,
        chart_base64: str,
        gen_chart_input: Dict[str, Any],
        suggestion_list: List[str],
    ) -> Dict[str, Any]:
        """
        VLM迭代反馈优化图表

        Args:
            chart_base64: 图表base64
            gen_chart_input: 图表生成任务输入信息

        Returns:
            Dict[str, Any]: 
            - 反馈意见内容
            - 图表得分
        """
        # 构建输入
        vlm_llm_input = {
            "chart_title": gen_chart_input.get("chart_title", ""),
            "chart_description": gen_chart_input.get("chart_description", ""),
            "chart_type": gen_chart_input.get("chart_type", ""),
            "chart_data": gen_chart_input.get("chart_data", {}),
            "history_suggestion": suggestion_list,
            "chart_base64": chart_base64,
        }

        try:
            call_model_input = CallModelInput(
                model_name=self._vlm_model,
                prompt="vlm_iterate_prompt",
                user_input=vlm_llm_input,
                agent_name=AgentLlmName.VLM_CHART_GENERATOR_VLM_ITERATE.value,
            )
            response = await call_model(call_model_input, use_vlm=True)
            if not response:
                return {}
            return response

        except Exception as e:
            error_msg = f"Error in VLM iteration: {e}"
            logger.error(error_msg)
            raise CustomValueException(
                StatusCode.CHART_VLM_GENERATION_ERROR.code,
                StatusCode.CHART_VLM_GENERATION_ERROR.errmsg.format(e=error_msg),
            ) from e
