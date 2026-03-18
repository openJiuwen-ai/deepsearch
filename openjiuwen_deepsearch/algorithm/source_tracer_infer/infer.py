# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
import asyncio
from typing import List, Dict, Tuple
import base64

from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_extract_info import ResearchInferPreprocess
from openjiuwen_deepsearch.algorithm.source_tracer_infer.number_node import NumberNode
from openjiuwen_deepsearch.algorithm.source_tracer_infer.supplement_graph import SupplementGraph
from openjiuwen_deepsearch.algorithm.source_tracer_infer.generate_html import GenerateHTML
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import (call_model, is_equal_length, 
                                                                              type_check, GraphInfo)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


class SourceTracerInfer:

    def __init__(self, context):
        self.context = context
        self.language = context.get("language", "zh-CN")
        self.model_name = context.get("llm_model_name", "")
        self.response = context.get("source_tracer_response", "")  # 经历溯源模块处理后的报告内容
        self.conclusion_with_records = context.get("conclusion_with_records", None)
        self.checker_infos = {"graph_infos": [], "search_records": []}  # 溯源校验所需数据

        self.node_number = NumberNode()
        self.supplement_graph = SupplementGraph(context.get("llm_model_name", ""))
        self.generate_html = GenerateHTML(context.get("language", "zh-CN"))

    async def run(self) -> Tuple[str, List[Dict], List[GraphInfo], str]:
        """执行溯源校验
        Returns:
            response: 处理后的报告文本
            infer_messages: 溯源推理输出字段
            check_infos: 溯源推理校验模块所需数据
            error: 错误信息
        """
        logger.info(f"[SOURCE TRACER INFER] run starting...")
        logger.debug("[SOURCE TRACER INFER] The response before Source Tracer Infer:\n %s", self.response)
        infer_messages = []
        error = None
        try:
            # 根据生成模式获取结论与搜索记录
            await self.get_conclusion_and_records()
            # 异步生成每个结论的推理图和图结构数据
            checked_infer_graphs = []
            final_conclusion_info = []
            task = [self.async_run(
                {"conclusion": item.get("conclusion", []), 
                 "search_records": item.get("search_records", [])}) for item in self.conclusion_with_records]
            results = await asyncio.gather(*task)
            for index, (infer_message, checked_infer_graph) in enumerate(results):
                if infer_message.get("html_base64", ""):
                    infer_message["id"] = index
                    infer_messages.append(infer_message)
                    checked_infer_graphs.append(checked_infer_graph)
                    final_conclusion_info.append(self.conclusion_with_records[index])

            # 标注文章中的推理内容
            response = self.mark_conclusion_in_report(infer_messages, final_conclusion_info)
            # 保存并构造溯源推理校验所需数据
            self.checker_infos["graph_infos"] = checked_infer_graphs
            self.checker_infos["search_records"] = [info.get("search_records", []) for info in final_conclusion_info]

        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(f"[SOURCE TRACER INFER] run error: **")
            else:
                logger.error(f"[SOURCE TRACER INFER] run error: {repr(e)}")
            error = str(e)
            response = self.response
            infer_messages = []
        logger.debug("[SOURCE TRACER INFER] The response after Source Tracer Infer:\n %s", response)
        logger.info(f"[SOURCE TRACER INFER] run end.")
        return response, infer_messages, self.checker_infos, error

    async def async_run(self, datas: Dict) -> Tuple[Dict, GraphInfo]:
        """异步执行每个结论推理图的绘制程序
        Args:
            datas: 单条结论和对应的搜索记录
        Returns:
            html_file_path: 结论生成的推理图相对路径
            checked_infer_graphs: 编号后最终的图抽象数据，包含（structured_inference, node_map, citation_ids, conclusion_ids)
        """
        logger.info(f"[SOURCE TRACER INFER] async_run starting...")
        checked_infer_graphs = None
        infer_message = {}
        try:
            # search_records中筛选与结论有关的引用
            conclusion_and_evidences = await self.extract_reference(datas)
            # 对conclusion进行推理
            inferences = await self.infer(conclusion_and_evidences)
            # 过滤无效、低质量推理
            inferences = await self.filter_invalid_infer(inferences)
            # 结构化inference
            structured_inferences = await self.structured_infer(inferences)
            # 给节点编号
            infer_graphs = self.node_number.number_node(structured_inferences, 
                                                        conclusion=conclusion_and_evidences.get("conclusion", ""), 
                                                        search_records=datas.get("search_records", []))
            # 删除自环，修复非连通
            checked_infer_graphs = await self.supplement_graph.run(infer_graphs)
            # 构建推理图
            html_content = self.generate_html.run(checked_infer_graphs)
            infer_message["conclusion"] = inferences.get("conclusion", "")
            infer_message["inference"] = inferences.get("inference", "")
            infer_message["html_base64"] = self._encode_html_to_base64(html_content)
        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = f"run error: **"
            else:
                error_msg = f"run error: {e}, the conclusion is: {inferences.get('conclusion', '')}"
            logger.warning(f"[SOURCE TRACER INFER] single conclusion infer error: {error_msg}")
            infer_message = {}
            checked_infer_graphs = None
        logger.info(f"[SOURCE TRACER INFER] run end.")
        return infer_message, checked_infer_graphs

    @staticmethod
    def _encode_html_to_base64(html_content: str):
        """将html标签语言转换为base64"""
        base64_string = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
        try:
            decoded_string = base64.b64decode(base64_string).decode('utf-8')
            is_valid = (decoded_string == html_content)

            if not is_valid:
                raise ValueError('Encode html to base64 failed.')

        except Exception as e:
            raise e
        return base64_string

    async def get_conclusion_and_records(self):
        """调用溯源推理信息提取模块，提取溯源推理所需数据，目前只有 research 模式"""
        if self.conclusion_with_records:
            return
        preprocessor = ResearchInferPreprocess(self.context)
        self.conclusion_with_records = await preprocessor.run()

    async def extract_reference(self, datas: Dict) -> Dict:
        """
        筛查search_records中与conclusion相关的引用
        Args:
            datas: dict={
            "conclusion": 原始结论,
            "search_records": 对应章节的搜索记录
            }
        Returns:
            dict={
            "conclusion": 真正结论,
            "references": 筛选后的参考文献
            }
        """
        logger.info(f"[SOURCE TRACER INFER] extract valid citations starting...")
        conclusions, search_records = datas.get("conclusion", []), datas.get("search_records", [])
        # 检测结论和搜索记录是否为空, 若为空，当前结论推理置空
        if not conclusions or not search_records:
            if LogManager.is_sensitive():
                logger.warning(
                    f"[SOURCE TRACER INFER]: conclusion: *** or search_records *** is None, skip current infer.")
            else:   
                logger.warning(
                    f"[SOURCE TRACER INFER]: conclusion: {conclusions[-1]} or search_records {search_records} is None, \
                    skip current infer.")
            return {}

        records = [{"id": index, "content": record.get("content", "")} for index, record in enumerate(search_records)]
        handle_datas = {"statement": conclusions[0], "references": records}  # 不包含最后一个主要结论
        detection_func_and_args = {"detection_func": type_check, "args": list}
        results = await call_model(self.model_name, "infer_validate_prompt", handle_datas, 
                                   detection_func_and_args=detection_func_and_args, 
                                   agent_name=NodeId.SOURCE_TRACER_INFER.value + "_extract_reference")
        if not results:
            logger.warning("[SOURCE TRACER INFER] No supported reference.")
            return {}

        try:
            references = []
            for index in results:
                if 0 <= index < len(search_records):
                    references.append({"id": index, "content": search_records[index].get("content", "")})
            evidence = {"conclusion": conclusions[-1], "reference": references}  # 最后一个是主要结论
            logger.debug(
                "[SOURCE TRACER INFER] extract supported references:\n %s", 
                json.dumps(evidence, ensure_ascii=False, indent=4))

        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = f"extract supported references error: ***"
            else:
                error_msg = f"extract supported references error: {str(e)}, the conclusion is: {conclusions[-1]}"
            logger.warning(f"[SOURCE TRACER INFER] {error_msg}")
            return {}
        logger.info(f"[SOURCE TRACER INFER] extract valid citations end.")
        return evidence

    async def infer(self, evidences: Dict) -> Dict:
        """
        对支撑材料与文本相关内容进行关联推理, llm根据支撑材料输出得出结论的推理过程
        Args:
            dict={
            "conclusion": 待推理的结论,
            "reference": 筛选后的参考文献
            }
        Returns:
            dict={
            "conclusion": 待推理的结论,
            "inference": 推理过程
            }
        """
        logger.info(f"[SOURCE TRACER INFER] infer start...")
        detection_func_and_args = {"detection_func": type_check, "args": list}
        results = await call_model(self.model_name, "infer_conclusion_prompt", evidences, 
                                   detection_func_and_args=detection_func_and_args, 
                                   agent_name=NodeId.SOURCE_TRACER_INFER.value + "_infer")
        inference = results[0] if (isinstance(results, list) and results) else ""
        results = {"conclusion": evidences.get("conclusion", ""), "inference": inference}
        logger.debug("[SOURCE TRACER INFER] infer result:\n %s", json.dumps(results, ensure_ascii=False, indent=4))
        logger.info(f"[SOURCE TRACER INFER] infer end.")
        return results

    async def filter_invalid_infer(self, inferences: Dict) -> Dict:
        """
        过滤掉无效的、质量较差的推理
        """
        logger.info(f"[SOURCE TRACER INFER] filter invalid inference starting...")
        input_inferences = inferences.get('inference', "")
        detection_func_and_args = {"detection_func": type_check, "args": str}
        results = await call_model(self.model_name, "infer_filter_inference_prompt", {"input": [input_inferences]}, 
                                   detection_func_and_args=detection_func_and_args, 
                                   agent_name=NodeId.SOURCE_TRACER_INFER.value + "_filter_invalid_infer")
        results = json.loads(results)
        if not results:
            if LogManager.is_sensitive():
                logger.warning(f"[SOURCE TRACER INFER] filter invalid inference: ***")
            else:
                logger.warning(f"[SOURCE TRACER INFER] filter invalid inference: {input_inferences}")
            raise ValueError("invalid inference")
        logger.info(f"[SOURCE TRACER INFER] infer filter inference ending.")
        return inferences

    async def structured_infer(self, inference: Dict) -> List[List]:
        """
        结构化inference，提取结构化参考材料的关系
        """
        logger.info(f"[SOURCE TRACER INFER] structured_infer starting...")
        detection_func_and_args = {"detection_func": is_equal_length, "args": 3} # 需要添加检测函数，检测输出的每个结构为三元组
        result = await call_model(self.model_name, "infer_structured_prompt", inference, 
                                  detection_func_and_args=detection_func_and_args, 
                                  agent_name=NodeId.SOURCE_TRACER_INFER.value + "_structured_infer")
        if not result:
            raise ValueError(f"unstructured inference!")
        logger.debug("[SOURCE TRACER INFER] structured_infer result:\n %s", 
                     json.dumps(result, ensure_ascii=False, indent=4))
        return result

    def mark_conclusion_in_report(self, infer_messages, conclusion_infos):
        """
        标注报告中的推理内容
        """
        logger.info(f"[SOURCE TRACER INFER] mark conclusion in report starting...")
        origin_response = self.response
        try:
            label_template = "[{conclusion}](#inference:{infer_id})"
            for conclusion_info, infer_message in zip(conclusion_infos, infer_messages):
                # 构造带标注的结论
                labeled_conclusion = label_template.format(conclusion=infer_message.get("conclusion", ""), 
                                                           infer_id=infer_message.get("id", -1))
                # 在原始文本中替换结论为带标注的结论
                self.response = self.response[:conclusion_info['start_pos']] + \
                                 labeled_conclusion + \
                                 self.response[conclusion_info['end_pos']:]
            logger.debug("[SOURCE TRACER INFER] report with marked inference conclusions:\n %s", self.response)
            logger.info(f"[SOURCE TRACER INFER] mark conclusion in report end.")
            return self.response
        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = f"mark conclusion in report error: ***"
            else:
                error_msg = f"mark conclusion in report error: {repr(e)}"
            logger.warning(f"[SOURCE TRACER INFER] {error_msg}")
            origin_response = self.response
        return origin_response
